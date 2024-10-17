import logging
from netmiko import ConnectHandler, file_transfer
from datetime import datetime
import os

# Setup logging
logging.basicConfig(filename='network_upgrade.log', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("upload")

class iostrain:
    def __init__(self, major=0, release_version=0, rebuild=0, special_release='0x00', release_designation='none'):
        self.major = major
        self.release_version = release_version
        self.rebuild = rebuild
        self.special_release = special_release
        self.release_designation = release_designation

class _ios:
    def __init__(self):
        self.running_ios = iostrain()

class _file:
    def __init__(self, file_name='', file_size=0, timestamp=''):
        self.file_name = file_name
        self.file_size = file_size
        self.timestamp = timestamp

class _disk:
    def __init__(self):
        self.total_capacity = 0
        self.free_space = 0
        self.files = []

class network_node:
    def __init__(self, node_name, node_ip_address, username, password):
        self.node_name = node_name
        self.node_ip_address = node_ip_address
        self.username = username
        self.password = password
        self.ios = _ios()
        self.disk = _disk()
        self.hardware_type = ""

        # Establish SSH connection
        device = {
            'device_type': 'cisco_xe',
            'host': self.node_ip_address,
            'username': self.username,
            'password': self.password,
        }

        try:
            logging.info(f"Connecting to {self.node_name} ({self.node_ip_address})")
            self.ssh_conn = ConnectHandler(**device,keepalive=60)
            logging.info(f"Connected to {self.node_name}")

            # Parse show version
            self.parse_show_version()

            # Parse bootflash
            self.parse_bootflash()

            # Check for newer IOS and handle the upgrade
            self.handle_upgrade()

            # Log done message
            logging.info(f"{self.node_name} : Done")

        except Exception as e:
            logging.error(f"Error connecting to {self.node_name}: {e}")
            raise

    def parse_show_version(self):
        _version = self.ssh_conn.send_command("show version")
        logging.info(f"Parsing 'show version' output for {self.node_name}")

        # Parsing IOS-XE version
        for line in _version.splitlines():
            if "Cisco IOS XE Software" in line:
                version_info = line.split("Version")[1].strip().split(".")
                self.ios.running_ios.major = int(version_info[0])
                self.ios.running_ios.release_version = int(version_info[1])
                rebuild_info = version_info[2]
                if "a" in rebuild_info:
                    self.ios.running_ios.rebuild = int(rebuild_info.split("a")[0])
                    self.ios.running_ios.special_release = "a"
                else:
                    self.ios.running_ios.rebuild = int(rebuild_info)

            # Parse hardware type
            hardware_candidates = ['WS-CS3650-48PS', 'ISR4331', 'WS-C3650-48FS-S', 
                                   'WS-C3850-24XS-S', 'WS-C2960X-48FPS-L', 'WS-C2960X-24PS-L', 'WS-C2960X-48LPS-L']
            for hw in hardware_candidates:
                if hw in line:
                    self.hardware_type = hw

        logging.info(f"Parsed IOS-XE version for {self.node_name}: {self.ios.running_ios.major}.{self.ios.running_ios.release_version}.{self.ios.running_ios.rebuild}{self.ios.running_ios.special_release}")
        logging.info(f"Hardware type detected for {self.node_name}: {self.hardware_type}")

    def parse_bootflash(self):
        dir_output = self.ssh_conn.send_command("dir bootflash:")
        logging.info(f"Parsing 'dir bootflash:' output for {self.node_name}")

        # Parse bootflash information
        for line in dir_output.splitlines():
            if "bytes total" in line:
                self.disk.total_capacity = int(line.split()[0])
            if "bytes free" in line:
                self.disk.free_space = int(line.split()[3].strip("(").strip(")"))
            if ".bin" in line:
                file_info = line.split()
                file_name = file_info[-1]
                file_size = int(file_info[2])
                timestamp = " ".join(file_info[3:6])
                self.disk.files.append(_file(file_name, file_size, timestamp))

        logging.info(f"Bootflash parsed for {self.node_name}: Free space {self.disk.free_space}, Total capacity {self.disk.total_capacity}")

    def handle_upgrade(self):
        # Check for a newer IOS image
        local_directory = f'IOS_IMAGES/{self.hardware_type}'
        latest_image = None
        current_ios = f"{self.ios.running_ios.major}.{self.ios.running_ios.release_version}.{self.ios.running_ios.rebuild}{self.ios.running_ios.special_release}"

        logging.info(f"Checking for newer IOS in {local_directory} for {self.node_name}")
        
        for file in os.listdir(local_directory):
            if file.endswith(".bin") and file > current_ios:
                latest_image = file

        if latest_image:
            logging.info(f"Newer IOS image found for {self.node_name}: {latest_image}")
            latest_image_path = os.path.join(local_directory, latest_image)

            # Check available space
            file_size = os.path.getsize(latest_image_path)
            if file_size < self.disk.free_space:
                logging.info(f"Transferring {latest_image} to {self.node_name} (sufficient space available)")
                
                # Use Netmiko file transfer
                transfer_dict = file_transfer(self.ssh_conn, source_file=latest_image_path, dest_file=f"{latest_image}", file_system="bootflash:",socket_timeout=100)
                
                if transfer_dict['file_verified']:
                    logging.info(f"File {latest_image} successfully transferred and verified on {self.node_name}")
                else:
                    logging.error(f"File verification failed for {latest_image} on {self.node_name}")
            else:
                logging.error(f"Not enough space on bootflash for {self.node_name}. Required: {file_size}, Available: {self.disk.free_space}")
                return

            # Update boot system and save config
            logging.info(f"Updating boot system and saving configuration for {self.node_name}")
            self.ssh_conn.save_config()
            boot_config_set = ["no boot system",f"boot system flash bootflash:{latest_image}"]
            self.ssh_conn.send_config_set(boot_config_set)
            self.ssh_conn.save_config()
            #self.ssh_conn.send_command("no boot system")
            #self.ssh_conn.send_command(f"boot system flash bootflash:/{latest_image}")
        else:
            logging.info(f"No newer IOS image found for {self.node_name}")

# Example usage
device = network_node("Router2", "127.0.0.1", "MyUser", "MyPass")
