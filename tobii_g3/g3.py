import json
import subprocess
import websocket
import requests
from functools import wraps
from base64 import b64encode

from time import sleep
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from ipaddress import ip_address, IPv4Address

from typing import List, Dict, Any, Optional

__all__ = [
    "G3Error",
    "G3TimeoutError",
    "G3NotConnectedError",
    "G3ConnectionError",
    "G3InvalidIdError",
    "G3ErrorResponse",
    "G3Client",
]


class ZeroconfListener(ServiceListener):
    _discovered_ips = []
    _discovered_ipv6s = []
    _discovered_servers = []

    @property
    def discovered_ips(self):
        return tuple(self._discovered_ips)

    @property
    def discovered_ipv6s(self):
        return tuple(self._discovered_ipv6s)

    @property
    def discovered_servers(self):
        return tuple(self._discovered_servers)

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        server = info.server
        if server[-1] == ".":
            server = server[:-1]
        self._discovered_servers.append(server)

        ip_list = info.parsed_scoped_addresses()
        for ip in ip_list:
            if ip not in self.discovered_ips and self.is_ipv4(ip):
                self._discovered_ips.append(ip)
            elif ip not in self.discovered_ipv6s and not self.is_ipv4(ip):
                self._discovered_ipv6s.append(ip)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        ip_list = info.parsed_addresses()
        for ip in ip_list:
            if ip in self.discovered_ips:
                self._discovered_ips.remove(ip)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    @staticmethod
    def is_ipv4(address: str) -> bool:
        try:
            return True if type(ip_address(address)) is IPv4Address else False
        except ValueError:
            return False


class G3Error(Exception):
    """Base class for all G3Client exceptions."""

    pass


class G3TimeoutError(G3Error):
    """Raised when G3Client can not establish a connection."""

    pass


class G3NotConnectedError(G3Error):
    """Raised when G3Client is not connected to glasses websocket."""

    pass


class G3ConnectionError(G3Error):
    """Raised when G3Client websocket encounters a closed connection or network error."""

    pass


class G3InvalidIdError(G3Error):
    """Raised when G3Client receives a response with a mismatched id."""

    pass


class G3ErrorResponse(G3Error):
    """Raised when G3Client receives an erorr response from the glasses."""

    pass


def requires_connection(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.connected:
            raise G3NotConnectedError(
                "G3Client does not have an active websocket connection."
            )
        else:
            return func(self, *args, **kwargs)

    return wrapper


class G3Client:
    __api_objects = [
        "calibrate",
        "neighborhood",
        "network",
        "recorder",
        "recordings",
        "rudimentary",
        "settings",
        "system",
        "upgrade",
        "webrtc",
    ]
    default_wifi_ip = "192.168.75.51"
    default_wifi_url = f"http://{default_wifi_ip}"

    def __init__(self, glasses_address: str):
        self._glasses_address = glasses_address
        self._id = 0
        self.ws = websocket.WebSocket()

    def __del__(self):
        self.disconnect()

    @property
    def glasses_address(self) -> str:
        return self._glasses_address or ""

    @glasses_address.setter
    def glasses_address(self, addr: str):
        if self.connected:
            self.disconnect()

        self._glasses_address = addr

    @property
    def url(self) -> str:
        return f"ws://{self.glasses_address}"

    @property
    def http_url(self) -> str:
        return f"http://{self.glasses_address}"

    @property
    def ws_url(self) -> str:
        return f"{self.url}/websocket/"

    @property
    def connected(self) -> bool:
        return self.ws.connected

    @staticmethod
    def discover_g3() -> Optional[str]:
        glasses_address = None
        # wifi AP does not seem to work with zeroconf. Just try the default AP IP addr first
        try:
            res = requests.get(G3Client.default_wifi_url, timeout=0.25)
            if res.ok:
                return G3Client.default_wifi_ip
        except Exception:
            pass

        # discover glasses with zeroconf
        timeout = 10
        sleep_interval = 0.1
        max_attempts = timeout / sleep_interval
        attempts = 0
        service_type = "_tobii-g3api._tcp.local."
        listener = ZeroconfListener()
        try:
            zeroconf = Zeroconf()
            browser = ServiceBrowser(zeroconf, service_type, listener)
            while (
                not (listener.discovered_servers or listener.discovered_ips)
                and attempts < max_attempts
            ):
                attempts += 1
                sleep(sleep_interval)
        finally:
            zeroconf.close()
            if listener.discovered_servers:
                glasses_address = listener.discovered_servers[
                    -1
                ]  # use last value (usually there will only be 1)
            elif listener.discovered_ips:
                glasses_address = listener.discovered_ips[-1]
            else:
                glasses_address = None

        return glasses_address

    def connect(self):
        if self.connected:
            self.ws.close()

        try:
            self.ws.connect(self.ws_url, subprotocols=["g3api"])
        except websocket.WebSocketTimeoutException as e:
            raise G3TimeoutError(
                "Timed out trying to connect to glasses server."
            ) from e

    def disconnect(self):
        if self.connected:
            self.ws.close()

    def _generate_ws_id(self) -> int:
        self._id += 1
        self._id %= 1024
        return self._id

    @requires_connection
    def _ws_recv(self):
        try:
            data = self.ws.recv()
        except websocket.WebSocketConnectionClosedException as e:
            raise G3ConnectionError from e
        return json.loads(data)

    @requires_connection
    def _ws_send(self, ws_json: str):
        try:
            self.ws.send(ws_json)
        except websocket.WebSocketConnectionClosedException as e:
            raise G3ConnectionError from e

    def _request_property(self, parent_path: str, property_name: str) -> Dict[str, Any]:
        id = self._generate_ws_id()
        ws_dict = {"path": f"{parent_path}.{property_name}", "id": id, "method": "GET"}
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return ws_dict

    def get_property(self, parent_path: str, property_name: str):
        request = self._request_property(parent_path, property_name)
        response = self._ws_recv()

        if "body" in response:
            body = response["body"]
        else:
            body = response

        if response["id"] == request["id"]:
            return body
        else:
            raise G3InvalidIdError(
                f"Response to get_property contained a mismatched id. {response}"
            )

    def _request_set_property(
        self, parent_path: str, property_name: str, value
    ) -> Dict[str, Any]:
        id = self._generate_ws_id()
        ws_dict = {
            "path": f"{parent_path}.{property_name}",
            "id": id,
            "method": "POST",
            "body": value,
        }
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return ws_dict

    def set_property(
        self, parent_path: str, property_name: str, value
    ) -> Dict[str, Any]:
        request = self._request_set_property(parent_path, property_name, value)
        response = self._ws_recv()

        if "body" in response:
            body = response["body"]
        else:
            body = response

        if not response["id"] == request["id"]:
            raise G3InvalidIdError(
                f"Response to get_property contained a mismatched id. {response}"
            )

        if body is False:
            raise G3ErrorResponse(
                f"Failed set-property: {parent_path}.{property_name}: {value}"
            )

        return body

    def _request_action(
        self, parent_path: str, action_name: str, action_val: Optional[List[Any]] = None
    ) -> Dict[str, Any]:

        if action_val is None:
            action_val = []

        id = self._generate_ws_id()
        ws_dict = {
            "path": f"{parent_path}!{action_name}",
            "id": id,
            "method": "POST",
            "body": action_val,
        }
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return ws_dict

    def send_action(
        self, parent_path: str, action_name: str, action_val: Optional[List[Any]] = None
    ):
        if action_val is None:
            action_val = []
        request = self._request_action(parent_path, action_name, action_val)
        response = self._ws_recv()

        if "body" in response:
            body = response["body"]
        else:
            body = response

        if not response["id"] == request["id"]:
            raise G3InvalidIdError(
                f"Response to send_action contained a mismatched id -> {response}"
            )

        if "error_info" in response:
            raise G3ErrorResponse(
                f"Error in send_action: {response['error_info']}", response
            )
        if "error" in response:
            raise G3ErrorResponse(
                f"Error {response['error']}: {response['message']}", response
            )

        if body is False:
            raise G3ErrorResponse(
                f"Failed send-action: {parent_path}!{action_name}: {action_val}"
            )

        return body

    def create_wifi_config(self, name: str):
        uuid = self.send_action("network/wifi", "create-config", [name])
        return uuid

    def config_wifi(self, uuid, ssid: str, psk: str):
        self.set_property(f"network/wifi/configurations/{uuid}", "ssid-name", ssid)
        self.set_property(f"network/wifi/configurations/{uuid}", "security", "wpa-psk")
        self.set_property(f"network/wifi/configurations/{uuid}", "psk", psk)
        self.send_action(f"network/wifi/configurations/{uuid}", "save")

    def connect_wifi(self, uuid):
        self.send_action(f"network/wifi", "connect", [uuid])

    def disconnect_wifi(self):
        self.send_action(f"network/wifi", "disconnect")

    def scan_wifi(self, uuid):
        self.send_action(f"network/wifi", "scan")

    def network_factory_reset(self):
        self.send_action(f"network", "reset")

    def _request_subscribe_signal(self, parent_path, signal_name) -> Dict[str, Any]:
        id = self._generate_ws_id()
        ws_dict = {
            "path": f"{parent_path}:{signal_name}",
            "id": id,
            "method": "POST",
            "body": [],
        }
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return ws_dict

    def subscribe_signal(self, parent_path, signal_name):
        request = self._request_subscribe_signal(parent_path, signal_name)
        response = self._ws_recv()

        body = response["body"]
        if not response["id"] == request["id"]:
            raise G3InvalidIdError(
                f"Response to subscribe_signal contained a mismatched id -> {response}"
            )
        return body

    @requires_connection
    def open_livestream(self):
        cmd = ["vlc", f"rtsp://{self.glasses_address}:8554/live/all"]
        subprocess.Popen(
            cmd,
            shell=False,
            stdin=None,
            stdout=None,
            stderr=None,
            close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS,
        )

    @property
    def battery_level(self):
        return self.get_property("system/battery", "level")

    @property
    def remaining_battery_time(self):
        return self.get_property("system/battery", "remaining-time")

    @property
    def battery_state(self):
        return self.get_property("system/battery", "state")

    @property
    def system_time(self):
        return self.get_property("system", "time")

    @property
    def system_timezone(self):
        return self.get_property("system", "timezone")

    @property
    def head_unit_serial(self):
        return self.get_property("system", "head-unit-serial")

    @property
    def recording_unit_serial(self):
        return self.get_property("system", "recording-unit-serial")

    @property
    def firmware_version(self):
        return self.get_property("system", "version")

    @property
    def sd_card_state(self):
        return self.get_property("system/storage", "card-state")

    @property
    def recording_uuid(self):
        return self.get_property("recorder", "uuid")

    @property
    def recording_folder(self):
        return self.get_property("recorder", "folder")

    @property
    def duration(self):
        return self.get_property("recorder", "duration")

    @property
    def is_recording(self):
        return self.get_property("recorder", "duration") != -1

    def emit_calibrate_markers(self):
        return self.send_action("calibrate", "emit-markers")

    def calibrate(self):
        return self.send_action("calibrate", "run")

    def start_recording(self):
        return self.send_action("recorder", "start")

    def stop_recording(self):
        return self.send_action("recorder", "stop")

    def set_folder_name(self, folder_name):
        """The recorder.folder property will be used to create a folder on a FAT32/exFAT
        file system and is restricted in length and in which characters are allowed.
        The following characters cannot be used: 0x00-0x1F 0x7F " * / : < > ? \ |.
        _ is also known not to work"""
        illegal_chars = [
            '"',
            "*",
            "/",
            ":",
            "<",
            ">",
            "?",
            "\\",
            "|",
            "_",
        ]  # bad printable characters
        for x in range(0x20):  # bad control characters 0x00 - 0x1f
            illegal_chars.append(chr(x))
        for c in illegal_chars:
            if c in folder_name:
                raise RuntimeError(f"Folder name can not include a '{c}'.")
        return self.set_property("recorder", "folder", folder_name)

    def set_visible_name(self, visible_name):
        # this name is saved as a key in recording.g3 file and is seen in the web interface or glasses app
        return self.set_property("recorder", "visible-name", visible_name)

    def meta_insert(self, key_name, byte_data):
        if type(byte_data) is str:
            byte_data = bytes(byte_data.encode("utf-8"))

        b64data = b64encode(byte_data)
        # meta_data = f'["{key_name}", "{b64data.decode("ascii")}"]'
        meta_data = [key_name, b64data.decode("ascii")]
        return self.send_action("recorder", "meta_insert", meta_data)

    def send_event(self, tag, data):
        return self.send_action("recorder", "send-event", [tag, data])

    def set_gaze_overlay(self, b_overlay=True):
        return self.set_property("settings", "gaze_overlay", b_overlay)

    def get_recording_url(self, uuid):
        recording_url = self.get_property(f"recordings/{uuid}", "http-path")
        return f"{self.http_url}{recording_url}"

    def get_recording_g3(self, uuid):
        g3_url = self.get_recording_url(uuid)
        response = requests.get(g3_url, timeout=0.5)
        if response.ok:
            return response.json()
        else:
            raise G3ErrorResponse(f"HTTP error: {response.reason}", response)

    def get_recording_gaze(self, uuid):
        base_url = self.get_recording_url(uuid)
        g3 = self.get_recording_g3(uuid)
        gaze_file = g3["gaze"]["file"]
        gaze_url = f"{base_url}/{gaze_file}"
        response = requests.get(
            gaze_url, params={"use-content-encoding": "true"}, timeout=0.5
        )
        gaze_list = []
        if response.ok:
            for line in response.text.splitlines():
                if line:
                    obj = json.loads(line)
                    gaze_list.append(obj)
            return gaze_list
        else:
            raise G3ErrorResponse(f"HTTP error: {response.reason}", response)

    def get_recording_events(self, uuid):
        base_url = self.get_recording_url(uuid)
        if base_url is None:
            return None

        g3 = self.get_recording_g3(uuid)
        event_file = g3["events"]["file"]
        event_url = f"{base_url}/{event_file}"
        response = requests.get(
            event_url, params={"use-content-encoding": "true"}, timeout=0.5
        )
        event_list = []
        if response.ok:
            for line in response.text.splitlines():
                if line:
                    obj = json.loads(line)
                    event_list.append(obj)
            return event_list
        else:
            raise G3ErrorResponse(f"HTTP error: {response.reason}", response)

    def get_recording_imu(self, uuid):
        base_url = self.get_recording_url(uuid)
        g3 = self.get_recording_g3(uuid)
        imu_file = g3["imu"]["file"]
        imu_url = f"{base_url}/{imu_file}"
        response = requests.get(
            imu_url, params={"use-content-encoding": "true"}, timeout=0.5
        )
        imu_list = []
        if response.ok:
            for line in response.text.splitlines():
                if line:
                    obj = json.loads(line)
                    imu_list.append(obj)
            return imu_list
        else:
            raise G3ErrorResponse(f"HTTP error: {response.reason}", response)
