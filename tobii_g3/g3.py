import websocket

# import threading
import json
from base64 import b64encode, b64decode
import random
import subprocess
import requests

from time import sleep
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from ipaddress import ip_address, IPv4Address


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
    _default_wifi_ip = "192.168.75.51"
    _default_wifi_url = f"http://{_default_wifi_ip}"

    def __init__(self, glasses_address=None):
        self.glasses_address = glasses_address
        if glasses_address is None:
            # try:
            self._discover_g3()
            # except:
            #    print(f"Error during device discovery")
        if self.glasses_address:
            try:
                self.connect()
            except:
                print(f"Error in connect")

    def __del__(self):
        self.disconnect()

    @property
    def url(self):
        return f"ws://{self.glasses_address}"

    @property
    def http_url(self):
        return f"http://{self.glasses_address}"

    @property
    def ws_url(self):
        return f"{self.url}/websocket/"

    @property
    def connected(self):
        try:
            return self.ws.connected
        except:
            return False

    def _discover_g3(self):

        # don't do anything if already connected
        if self.connected:
            print(f"Glasses already discovered and connected")
            return

        # wifi AP does not seem to work with zeroconf. Just try the default AP IP addr first
        try:
            res = requests.get(self._default_wifi_url, timeout=0.25)
            if res.ok:
                self.glasses_address = self._default_wifi_ip
                print(f"Discovered glasses at {self.glasses_address}")
                return
        except:
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
                if attempts == max_attempts:
                    print("Timed out while searching for glasses")
                else:
                    RuntimeWarning("Unknown error in _discover_g3")

            if glasses_address:
                self.glasses_address = glasses_address
                print(f"Discovered glasses at {glasses_address}")

    def connect(self):
        self.ws = websocket.create_connection(self.ws_url, subprotocols=["g3api"])
        if self.connected:
            print("connected to websocket")

    def disconnect(self):
        if self.connected:
            self.ws.close()

    def _generate_ws_id(self):
        return random.randint(0, 99)

    def _ws_recv(self):
        if self.connected:
            return json.loads(self.ws.recv())
        else:
            raise ConnectionError

    def _ws_send(self, ws_json):
        if self.connected:
            self.ws.send(ws_json)
        else:
            raise ConnectionError

    def _request_property(self, parent_path, property_name):
        id = self._generate_ws_id()
        ws_dict = {"path": f"{parent_path}.{property_name}", "id": id, "method": "GET"}
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return id

    def get_property(self, parent_path, property_name):
        id = self._request_property(parent_path, property_name)
        response = self._ws_recv()

        f_response_match = response["id"] == id
        if "body" in response:
            body = response["body"]
        else:
            body = response

        if f_response_match:
            return body
        else:
            print("Warning, received mismatched id in get_property")
            return None

    def _request_set_property(self, parent_path, property_name, value):
        id = self._generate_ws_id()
        ws_dict = {
            "path": f"{parent_path}.{property_name}",
            "id": id,
            "method": "POST",
            "body": value,
        }
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return id

    def set_property(self, parent_path, property_name, value):
        id = self._request_set_property(parent_path, property_name, value)
        response = self._ws_recv()

        f_response_match = response["id"] == id
        if "body" in response:
            body = response["body"]
        else:
            body = response

        if not f_response_match:
            print("Warning, received mismatched id in set_property")

        return (f_response_match, body)

    def _request_action(self, parent_path, action_name, action_val=[]):
        id = self._generate_ws_id()
        ws_dict = {
            "path": f"{parent_path}!{action_name}",
            "id": id,
            "method": "POST",
            "body": action_val,
        }
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return id

    def send_action(self, parent_path, action_name, action_val=[]):
        id = self._request_action(parent_path, action_name, action_val)
        response = self._ws_recv()

        f_response_match = response["id"] == id
        if "body" in response:
            body = response["body"]
        else:
            body = response

        if not f_response_match:
            print("Warning, received mismatched id in send_action")

        if "error_info" in response:
            print(f"Error in send_action: {response['error_info']}")
        if "error" in response:
            print(f"Error {response['error']}: {response['message']}")

        return (f_response_match, body)

    def _request_subscribe_signal(self, parent_path, signal_name):
        id = self._generate_ws_id()
        ws_dict = {
            "path": f"{parent_path}:{signal_name}",
            "id": id,
            "method": "POST",
            "body": [],
        }
        ws_json = json.dumps(ws_dict)
        self._ws_send(ws_json)
        return id

    def subscribe_signal(self, parent_path, signal_name):
        self._request_subscribe_signal(parent_path, signal_name)
        response = self._ws_recv()

        f_response_match = response["id"] == id
        body = response["body"]
        if not f_response_match:
            print("Warning, received mismatched id in subscribe_signal")
        return (f_response_match, body)

    def open_livestream(self):
        if self.connected:
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
        else:
            raise ConnectionError

    @property
    def battery_level(self):
        try:
            return self.get_property("system/battery", "level")
        except:
            return None

    @property
    def remaining_battery_time(self):
        try:
            return self.get_property("system/battery", "remaining-time")
        except:
            return None

    @property
    def battery_state(self):
        try:
            return self.get_property("system/battery", "state")
        except:
            return None

    @property
    def system_time(self):
        try:
            return self.get_property("system", "time")
        except:
            return None

    @property
    def system_timezone(self):
        try:
            return self.get_property("system", "timezone")
        except:
            return None

    @property
    def head_unit_serial(self):
        try:
            return self.get_property("system", "head-unit-serial")
        except:
            return None

    @property
    def recording_unit_serial(self):
        try:
            return self.get_property("system", "recording-unit-serial")
        except:
            return None

    @property
    def firmware_version(self):
        try:
            return self.get_property("system", "version")
        except:
            return None

    @property
    def sd_card_state(self):
        try:
            return self.get_property("system/storage", "card-state")
        except:
            return None

    def emit_calibrate_markers(self):
        return self.send_action("calibrate", "emit-markers")

    def calibrate(self):
        return self.send_action("calibrate", "run")

    def start_recording(self):
        return self.send_action("recorder", "start")

    def stop_recording(self):
        return self.send_action("recorder", "stop")

    def set_folder_name(self, folder_name):
        # does this even work??
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
        if recording_url[0]:
            recording_url = recording_url[1]
        else:
            return None
        full_url = f"{self.http_url}{recording_url}"
        return full_url

    def get_recording_g3(self, uuid):
        g3_url = self.get_recording_url(uuid)
        if g3_url is None:
            return None

        try:
            response = requests.get(g3_url, timeout=0.5)
            if response.ok:
                return response.json()
            else:
                print(f"HTTP error: {response.reason}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"HTTP exception: {e}")
            return None

    def get_recording_gaze(self, uuid):
        base_url = self.get_recording_url(uuid)
        if base_url is None:
            return None

        g3 = self.get_recording_g3(uuid)
        gaze_file = g3["gaze"]["file"]
        gaze_url = f"{base_url}/{gaze_file}"
        try:
            res = requests.get(
                gaze_url, params={"use-content-encoding": "true"}, timeout=0.5
            )
            gaze_list = []
            if res.ok:
                for line in res.text.splitlines():
                    if line:
                        obj = json.loads(line)
                        gaze_list.append(obj)
                return gaze_list
            else:
                print(f"HTTP error: {res.reason}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"HTTP exception: {e}")
            return None

    def get_recording_events(self, uuid):
        base_url = self.get_recording_url(uuid)
        if base_url is None:
            return None

        g3 = self.get_recording_g3(uuid)
        event_file = g3["events"]["file"]
        event_url = f"{base_url}/{event_file}"
        try:
            res = requests.get(
                event_url, params={"use-content-encoding": "true"}, timeout=0.5
            )
            event_list = []
            if res.ok:
                for line in res.text.splitlines():
                    if line:
                        obj = json.loads(line)
                        event_list.append(obj)
                return event_list
            else:
                print(f"HTTP error: {res.reason}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"HTTP exception: {e}")
            return None

    def get_recording_imu(self, uuid):
        base_url = self.get_recording_url(uuid)
        if base_url is None:
            return None

        g3 = self.get_recording_g3(uuid)
        imu_file = g3["imu"]["file"]
        imu_url = f"{base_url}/{imu_file}"
        try:
            res = requests.get(
                imu_url, params={"use-content-encoding": "true"}, timeout=0.5
            )
            imu_list = []
            if res.ok:
                for line in res.text.splitlines():
                    if line:
                        obj = json.loads(line)
                        imu_list.append(obj)
                return imu_list
            else:
                print(f"HTTP error: {res.reason}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"HTTP exception: {e}")
            return None
