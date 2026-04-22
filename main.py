"""Forescout Plugin."""
import json
import os
import time
import traceback
import ipaddress
import requests
from datetime import datetime, timezone
from typing import Dict
import urllib.parse
from pydantic import ValidationError
from netskope.integrations.iot.models.asset import Asset
from netskope.common.utils import add_user_agent
from netskope.integrations.iot.plugin_base import (
    IotPluginBase,
    ValidationResult,
)


MAX_RETRY_COUNT = 4
LIMIT = 1000
PLATFORM_NAME = "Forescout"
MODULE_NAME = "IoT"
PLUGIN_VERSION = "1.0.0"
DEFAULT_TIMEOUT = 300
CHUNK_SIZE = 1000


class ForescoutException(Exception):
    """ForescoutException exception class."""
    pass


class ForescoutPlugin(IotPluginBase):
    """Forescout plugin class."""

    def __init__(
        self,
        name,
        *args,
        **kwargs,
    ):
        """Initialize Forescout plugin class."""
        super().__init__(
            name,
            *args,
            **kwargs,
        )
        self.plugin_name, self.plugin_version = self._get_plugin_info()
        self.log_prefix = f"{MODULE_NAME} {self.plugin_name}"
        if name:
            self.log_prefix = f"{self.log_prefix} [{name}]"

    def _get_plugin_info(self) -> tuple:
        """Get plugin name and version from manifest.

        Returns:
            tuple: Tuple of plugin's name and version fetched from manifest.
        """
        try:
            file_path = os.path.join(
                str(os.path.dirname(os.path.abspath(__file__))),
                "manifest.json",
            )
            with open(file_path, "r") as manifest:
                manifest_json = json.load(manifest)
                plugin_name = manifest_json.get("name", PLATFORM_NAME)
                plugin_version = manifest_json.get("version", PLUGIN_VERSION)
                return (plugin_name, plugin_version)
        except Exception as exp:
            self.logger.error(
                message=(
                    f"{MODULE_NAME} {PLATFORM_NAME}: Error occurred while"
                    " getting plugin details. Error: {}.".format(exp)
                ),
                details=traceback.format_exc(),
                error_code="IoT_PLUGIN_150000",
            )
        return (PLATFORM_NAME, PLUGIN_VERSION)

    def _add_user_agent(self, headers=None) -> Dict:
        """Add User-Agent in the headers of any request.

        Returns:
            Dict: Dictionary containing the User-Agent.
        """
        headers = add_user_agent(headers)
        ce_added_agent = headers.get("User-Agent", "netskope-ce")
        user_agent = "{}-{}-{}-v{}".format(
            ce_added_agent,
            MODULE_NAME.lower(),
            PLATFORM_NAME.replace(" ", "-").lower(),
            PLUGIN_VERSION,
        )
        headers.update({"User-Agent": user_agent})
        return headers

    def handle_error(self, response, logger_msg):
        """Handle API Status code errors.

        Args:
            response (Requests response object): Response object of requests.
            logger_msg (str): Logger message.
        """
        if response.status_code in [200, 201]:
            return response.json()
        elif response.status_code in [401, 403]:
            err_msg = (
                "Received exit code {} while {}. "
                "Verify configuration parameters or "
                "permissions provided.".format(
                    response.status_code, logger_msg
                )
            )
            error_code = "IoT_PLUGIN_150018"
        elif response.status_code >= 400 and response.status_code < 500:
            err_msg = (
                "Received exit code {}, "
                "HTTP Client error while {}.".format(
                    response.status_code, logger_msg
                )
            )
            error_code = "IoT_PLUGIN_150019"
        elif response.status_code >= 500 and response.status_code < 600:
            err_msg = (
                "Received exit code {}. "
                "HTTP Server error while {}.".format(
                    response.status_code, logger_msg
                )
            )
            error_code = "IoT_PLUGIN_150020"
        else:
            err_msg = (
                "Received exit code {}, "
                "HTTP error while {}.".format(
                    response.status_code, logger_msg
                )
            )
            error_code = "IoT_PLUGIN_150021"

        self.logger.error(
            message=f"{self.log_prefix}: {err_msg}",
            details=f"Received API response: {response.text}",
            error_code=error_code,
        )
        raise ForescoutException(err_msg)

    def _api_helper(self, request, logger_msg, is_handle_error_required=True):
        """Helper function for API calls with retry logic."""
        try:
            for retry_counter in range(MAX_RETRY_COUNT):
                response = request()
                if response.status_code == 429 or (
                    response.status_code >= 500 and response.status_code < 600
                ):
                    if retry_counter == MAX_RETRY_COUNT - 1:
                        if response.status_code == 429:
                            err_msg = (
                                "Received response code {}, max retries limit "
                                "exceeded while {}. Hence exiting.".format(
                                    response.status_code,
                                    logger_msg,
                                )
                            )
                            error_code = "IoT_PLUGIN_150012"
                        else:
                            err_msg = (
                                "Received response code {}, while {}. "
                                "Hence exiting.".format(
                                    response.status_code,
                                    logger_msg,
                                )
                            )
                            error_code = "IoT_PLUGIN_150013"
                        self.logger.error(
                            message=f"{self.log_prefix}: {err_msg}",
                            details=f"Received API response: {response.text}",
                            error_code=error_code,
                        )
                        raise ForescoutException(err_msg)
                    retry_after = response.headers.get("Retry-After")
                    remaining_retry = MAX_RETRY_COUNT - 1 - retry_counter
                    if retry_after is None:
                        self.logger.info(
                            "{}: No Retry-After value received from "
                            "API, hence plugin will retry after 60 "
                            "seconds. {} retries remaining.".format(
                                self.log_prefix,
                                remaining_retry,
                            )
                        )
                        time.sleep(60)
                        continue
                    retry_after = int(retry_after)
                    if retry_after > 300:
                        err_msg = (
                            "Received response code {}, 'Retry-After' value "
                            "received from response headers while {} is "
                            "greater than 5 minutes. Hence exiting.".format(
                                response.status_code,
                                logger_msg,
                            )
                        )
                        self.logger.error(
                            message=f"{self.log_prefix}: {err_msg}",
                            details=f"Received API response: {response.text}",
                            error_code="IoT_PLUGIN_150011",
                        )
                        raise ForescoutException(err_msg)

                    if response.status_code == 429:
                        self.logger.error(
                            message=(
                                "{}: Received response code {}, max retries "
                                "limit exceeded while {}. Retrying after {} "
                                "seconds. {} retries remaining.".format(
                                    self.log_prefix,
                                    response.status_code,
                                    logger_msg,
                                    retry_after,
                                    remaining_retry,
                                )
                            ),
                            details=f"Received API response: {response.text}",
                            error_code="IoT_PLUGIN_150009",
                        )
                    else:
                        self.logger.error(
                            message=(
                                "{}: Received response code {}, while {}. "
                                "Retrying after {} "
                                "seconds. {} retries remaining.".format(
                                    self.log_prefix,
                                    response.status_code,
                                    logger_msg,
                                    retry_after,
                                    remaining_retry,
                                )
                            ),
                            details=f"Received API response: {response.text}",
                            error_code="IoT_PLUGIN_150010",
                        )
                    time.sleep(retry_after)

                else:
                    return (
                        self.handle_error(response, logger_msg)
                        if is_handle_error_required
                        else response
                    )
        except json.JSONDecodeError as err:
            err_msg = "Invalid JSON response received from API."
            self.logger.error(
                message=f"{self.log_prefix}: {err_msg} Error: {err}.",
                details=f"Received API response {response.text}",
                error_code="IoT_PLUGIN_150014",
            )
            raise ForescoutException(err_msg)
        except requests.exceptions.ConnectionError as exp:
            err_msg = (
                "Unable to establish connection with {} "
                "platform while {}. {} is not reachable or "
                "invalid URL provided. Error: {}.".format(
                    PLATFORM_NAME, logger_msg, PLATFORM_NAME, exp
                )
            )
            toast_msg = (
                "{} is not reachable or "
                "invalid URL provided.".format(PLATFORM_NAME)
            )
            self.logger.error(
                message=f"{self.log_prefix}: {err_msg}",
                details=traceback.format_exc(),
                error_code="IoT_PLUGIN_150015",
            )
            raise ForescoutException(toast_msg)
        except requests.exceptions.RequestException as exp:
            err_msg = (
                "Error occurred while requesting"
                " to {} server for {}. Error: {}.".format(
                    PLATFORM_NAME, logger_msg, exp
                )
            )
            self.logger.error(
                message=f"{self.log_prefix}: {err_msg}",
                details=traceback.format_exc(),
                error_code="IoT_PLUGIN_150016",
            )
            toast_msg = "Request exception occurred."
            raise ForescoutException(toast_msg)
        except ForescoutException as err:
            raise err
        except Exception as exp:
            err_msg = (
                "Exception occurred while making API call to"
                " {} server while {}. Error: {}.".format(
                    PLATFORM_NAME, logger_msg, exp
                )
            )
            self.logger.error(
                message=f"{self.log_prefix}: {err_msg}",
                details=traceback.format_exc(),
                error_code="IoT_PLUGIN_150017",
            )
            raise exp

    def _validate_url(self, url: str) -> bool:
        """Validate the URL using parsing.

        Args:
            url (str): Given URL.

        Returns:
            bool: True or False { Valid or not Valid URL }.
        """
        parsed = urllib.parse.urlparse(url.strip())
        return (
            parsed.scheme.strip() != ""
            and parsed.netloc.strip() != ""
            and (parsed.path.strip() == "/" or parsed.path.strip() == "")
        )

    def validate_field(self, field_name, field_value, limit, invalid_fields):
        """Validate asset fields."""
        if field_value is not None and not isinstance(field_value, str):
            field_value = str(field_value)
        if not field_value or (
            field_value
            and (
                (len(field_value) > limit)
                or (len(field_value) < 2 and field_name != "os_version")
            )
        ):
            invalid_fields.append(field_name)
            return None
        return field_value

    def is_valid_mac(self, mac_address: str) -> bool:
        """Validate MAC address format."""
        if not mac_address:
            return False
        # Split the MAC address into parts
        parts = mac_address.split(":")

        # Check if the MAC address has 6 parts
        if len(parts) != 6:
            return False

        # Check each part to be a valid hexadecimal number
        for part in parts:
            try:
                int(part, 16)
            except ValueError:
                return False

        return True

    def _is_valid_ipv4(self, address: str) -> bool:
        """Validate IPv4 address.

        Args:
            address (str): Address to validate.

        Returns:
            bool: True if valid else False.
        """
        try:
            if not address:
                return False
            ipaddress.IPv4Address(address)
            return True
        except Exception:
            return False

    def _is_private_ipv4(self, address: str) -> bool:
        """Check if an IPv4 address is a private (RFC 1918) address.

        Args:
            address (str): Address to check.

        Returns:
            bool: True if private else False.
        """
        try:
            return ipaddress.IPv4Address(address).is_private and not (
                ipaddress.IPv4Address(address).is_loopback
                or ipaddress.IPv4Address(address).is_link_local
            )
        except Exception:
            return False

    def is_valid_timestamp(
        self, timestamp, format, invalid_fields, field_name
    ):
        """Validate and normalize a timestamp string.

        Args:
            timestamp (str): Timestamp string to validate.
            format (str): Expected datetime format string.
            invalid_fields (list): List to append field name if invalid.
            field_name (str): Name of the field being validated.

        Returns:
            str: ISO 8601 formatted timestamp or None if invalid.
        """
        try:
            if not timestamp:
                invalid_fields.append(field_name)
                return None
            ts = str(timestamp).strip()
            # Normalise common ISO 8601 variants
            if ts.endswith("Z"):
                ts = ts[:-1]
            if "." in ts:
                ts = ts[: ts.index(".")]
            dt = datetime.strptime(ts, format).replace(microsecond=0)
            return (
                datetime.fromisoformat(dt.isoformat())
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
        except Exception:
            invalid_fields.append(field_name)
            return None

    def validate(self, configuration: dict) -> ValidationResult:
        """Validate the Plugin configuration parameters.

        Args:
            configuration (dict): Configuration dictionary.

        Returns:
            ValidationResult: ValidationResult object with success flag and
                            message.
        """
        base_url = configuration.get("base_url", "").strip().strip("/")
        api_key = configuration.get("api_key")

        if "base_url" not in configuration or not base_url:
            err_msg = "Forescout URL is a required field."
            self.logger.error(
                f"{self.log_prefix}: Validation error occurred. Error: {err_msg}",
                error_code="IoT_PLUGIN_150001",
            )
            return ValidationResult(success=False, message=err_msg)
        elif not isinstance(base_url, str) or not self._validate_url(base_url):
            self.logger.error(
                "{}: Validation error occurred. Error: "
                "Invalid {} URL in the configuration parameters.".format(
                    self.log_prefix, PLATFORM_NAME
                ),
                error_code="IoT_PLUGIN_150002",
            )
            return ValidationResult(
                success=False,
                message="Invalid Forescout URL provided.",
            )
        if "api_key" not in configuration or not api_key:
            err_msg = "API Key is a required field."
            self.logger.error(
                f"{self.log_prefix}: Validation error occurred. Error: {err_msg}",
                error_code="IoT_PLUGIN_150003",
            )
            return ValidationResult(success=False, message=err_msg)

        return self.validate_auth(configuration=configuration)

    def validate_auth(self, configuration: dict) -> ValidationResult:
        """Validate credentials with Forescout platform.

        Args:
            configuration (dict): Configuration dictionary.

        Returns:
            ValidationResult: ValidationResult object having validation
            results after making an API call.
        """
        base_url = configuration.get("base_url", "").strip().strip("/")
        api_key = configuration.get("api_key")
        try:
            headers = self._add_user_agent()
            headers["X-Api-Key"] = api_key
            response = self._api_helper(
                lambda: requests.get(
                    url=f"{base_url}/api/data-exchange/v3/rem-assets",
                    headers=headers,
                    verify=self.ssl_validation,
                    timeout=DEFAULT_TIMEOUT,
                ),
                "validating configuration parameters",
                False,
            )

            if response.status_code == 200:
                return ValidationResult(
                    success=True, message="Validation successful."
                )
            elif response.status_code == 401:
                err_msg = "Invalid API Key provided."
                self.logger.error(
                    message=f"{self.log_prefix}: {err_msg}",
                    details=f"Received API response: {response.text}",
                    error_code="IoT_PLUGIN_150004",
                )
                return ValidationResult(success=False, message=err_msg)
            elif response.status_code == 403:
                err_msg = (
                    "The user does not have enough "
                    "permissions to configure plugin."
                )
                self.logger.error(
                    message=f"{self.log_prefix}: {err_msg}",
                    details=f"Received API response: {response.text}",
                    error_code="IoT_PLUGIN_150005",
                )
                return ValidationResult(success=False, message=err_msg)
            else:
                msg = (
                    "Validation error occurred. "
                    "Check logs for more details."
                )
                self.logger.error(
                    message=(
                        "{}: Validation error occurred with "
                        "response code {}.".format(
                            self.log_prefix, response.status_code
                        )
                    ),
                    details=f"Received API response: {response.text}",
                    error_code="IoT_PLUGIN_150006",
                )
                return ValidationResult(success=False, message=msg)

        except requests.exceptions.ConnectionError:
            self.logger.error(
                f"{self.log_prefix}: Validation Error, "
                "unable to establish connection with "
                f"{PLATFORM_NAME} Platform API.",
                error_code="IoT_PLUGIN_150029",
            )
            return ValidationResult(
                success=False,
                message="Validation Error, unable to establish connection "
                        "with API.",
            )
        except requests.HTTPError as err:
            self.logger.error(
                message=(
                    f"{self.log_prefix}: Validation Error, "
                    f"Error while validating credentials. Error: {err}."
                ),
                details=traceback.format_exc(),
                error_code="IoT_PLUGIN_150030",
            )
        except ForescoutException as exp:
            self.logger.error(
                message="{}: Validation error occurred. Error: {}".format(
                    self.log_prefix, exp
                ),
                details=traceback.format_exc(),
                error_code="IoT_PLUGIN_150007",
            )
            return ValidationResult(success=False, message=str(exp))
        except Exception as exp:
            err_msg = "Validation error occurred."
            self.logger.error(
                message=f"{self.log_prefix}: {err_msg} Error: {exp}.",
                details=traceback.format_exc(),
                error_code="IoT_PLUGIN_150008",
            )
            return ValidationResult(
                success=False,
                message=f"{err_msg} Check logs for more details.",
            )

        return ValidationResult(
            success=False,
            message="Validation Error, Check logs for more details.",
        )

    def get_assets(self, record):
        """Create Asset object from a single API record.

        Args:
            record (dict): Single record from API response.

        Returns:
            Asset: Asset object created from the record, or None if error.
        """
        invalid_fields = []
        asset = None
        try:

            # source_id
            source_id = self.validate_field(
                "source_id",
                record.get("id"),
                128,
                invalid_fields,
            )

            # IP address
            ip_address = None
            _ip_raw = record.get("ip_addresses")
            if isinstance(_ip_raw, list) and _ip_raw:
                _valid_ips = []
                for _ip_val in _ip_raw:
                    _ip_str = str(_ip_val).strip() if _ip_val else ""
                    if _ip_str and self._is_valid_ipv4(_ip_str):
                        _valid_ips.append(_ip_str)
                # Prefer private (RFC 1918) IPs over public
                for _candidate in _valid_ips:
                    if self._is_private_ipv4(_candidate):
                        ip_address = _candidate
                        break
                if not ip_address and _valid_ips:
                    ip_address = _valid_ips[0]
            elif isinstance(_ip_raw, str) and _ip_raw:
                if self._is_valid_ipv4(_ip_raw):
                    ip_address = _ip_raw
            if not ip_address:
                invalid_fields.append("ip")

            # tags
            _tags_raw = record.get("labels")
            tags = _tags_raw if isinstance(_tags_raw, list) else []

            # MAC address
            mac_address = None
            _mac_raw = record.get("mac_addresses")
            if isinstance(_mac_raw, list) and _mac_raw:
                for _mac_val in _mac_raw:
                    _mac_str = str(_mac_val).strip() if _mac_val else ""
                    if _mac_str and self.is_valid_mac(_mac_str):
                        mac_address = _mac_str.upper()
                        break
            elif isinstance(_mac_raw, str) and _mac_raw:
                mac_address = _mac_raw
            if not mac_address or not self.is_valid_mac(mac_address):
                invalid_fields.append("mac_address")
                mac_address = None

            # category
            category = self.validate_field(
                "category",
                record.get("rem_category"),
                32,
                invalid_fields,
            )

            # os
            os = self.validate_field(
                "os",
                record.get("rem_os"),
                128,
                invalid_fields,
            )

            # manufacturer (optional — extract from nested structure)
            manufacturer = None
            _mfr_raw = record.get("rem_vendor")
            try:
                if isinstance(_mfr_raw, str) and _mfr_raw.startswith("["):
                    import ast as _ast
                    _mfr_raw = _ast.literal_eval(_mfr_raw)
                if isinstance(_mfr_raw, list):
                    for _cat in _mfr_raw:
                        if isinstance(_cat, dict):
                            _cd = _cat.get("CategoryData") or _cat
                            _val = _cd.get("Manufacturer") or _cd.get("manufacturer")
                            if _val:
                                manufacturer = _val
                                break
                elif isinstance(_mfr_raw, str) and _mfr_raw:
                    manufacturer = _mfr_raw
            except Exception:
                pass
            if manufacturer:
                manufacturer = self.validate_field(
                    "manufacturer", manufacturer, 64, invalid_fields
                )

            if invalid_fields:
                self.logger.warn(
                    f"{self.log_prefix}: Skipping below fields"
                    " due to invalid values. "
                    f"Fields: '{', '.join(invalid_fields)}'."
                )
            asset = Asset(
                source_id=source_id or None,
                ip=ip_address or None,
                mac_address=mac_address or None,
                category=category or None,
                os=os or None,
                manufacturer=manufacturer or None,
                use_asset=True,
            )
            if tags:
                asset.tags = tags
        except (ValidationError, Exception) as error:
            err_message = (
                "Validation error occurred"
                if isinstance(error, ValidationError)
                else "Unexpected error occurred"
            )
            error_message = (
                f"{self.log_prefix}: {err_message} while "
                "creating asset for record"
            )
            message = ""
            if mac_address and source_id:
                message = (
                    f" with mac_address: {mac_address} "
                    f"and source_id: {source_id}"
                )
            elif mac_address:
                message = f" with mac_address: {mac_address}"
            elif source_id:
                message = f" with source_id: {source_id}"

            self.logger.warn(
                f"{error_message}{message}. "
                f"Hence skipping this asset. Error: {error}."
            )
        return asset

    def get_chunks(self, data, n_chunks):
        """Yield successive n_chunks sized chunks from list of data.

        Args:
            data: List to be divided in chunks.
            n_chunks: Length of resultant list after division.
        """
        for i in range(0, len(data), n_chunks):
            if i + n_chunks < len(data):
                yield data[i : i + n_chunks], False
            else:
                yield data[i : i + n_chunks], True

    def pull(self):
        """Pull assets from Forescout.

        Yields:
            tuple: (assets_list, is_first, is_last, asset_count, vuln_count)
        """
        base_url = self.configuration.get("base_url", "").strip().strip("/")
        api_key = self.configuration.get("api_key")

        # POST-based pagination with page number (e.g. Forescout)
        import time as _time
        current_time_ms = int(_time.time() * 1000)
        lookback_ms = 24 * 60 * 60 * 1000
        page_number = 0
        post_body = {
            "from_utc_millis": current_time_ms - lookback_ms,
            "to_utc_millis": current_time_ms,
            "selected_fields": ['id', 'ip_addresses', 'labels', 'mac_addresses', 'rem_category', 'rem_os', 'rem_vendor'],
            "page_number": page_number,
        }

        is_first, is_last = True, False
        headers = self._add_user_agent()
        headers["X-Api-Key"] = api_key
        headers["Content-Type"] = "application/json"

        while True:
            try:
                response = self._api_helper(
                    lambda: requests.post(
                        url=f"{base_url}/api/data-exchange/v3/rem-assets",
                        json=post_body,
                        headers=headers,
                        verify=self.ssl_validation,
                        timeout=DEFAULT_TIMEOUT,
                    ),
                    "fetching assets from forescout platform",
                    True,
                )

                assets = []
                for record in response.get("results") or []:
                    asset = self.get_assets(record)
                    if asset:
                        assets.append(asset)

                # Page number POST pagination
                if not response.get("results"):
                    is_last = True
                    yield assets, is_first, is_last, len(assets), 0
                    break
                if len(assets) < LIMIT:
                    is_last = True
                    yield assets, is_first, is_last, len(assets), 0
                    break
                else:
                    yield assets, is_first, is_last, len(assets), 0
                    is_first = False
                    page_number += 1
                    post_body["page_number"] = page_number

            except ForescoutException as exp:
                raise exp
            except Exception as exp:
                err_msg = "Error occurred."
                self.logger.error(
                    message=f"{self.log_prefix}: {err_msg} Error: {exp}.",
                    details=traceback.format_exc(),
                    error_code="IoT_PLUGIN_150022",
                )
                raise exp