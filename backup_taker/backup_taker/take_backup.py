import configparser
import os
import subprocess
import shutil
import logging
import datetime
import sys
import argparse
import glob
import hashlib
import requests
import platform
import gnupg


# Get arguments from args.
parser = argparse.ArgumentParser(description="Backup for ddmail")
parser.add_argument('--config-file', type=str, help='Full path to config file.', required=True)
args = parser.parse_args()

# Check that config file exsist and is a file.
if os.path.isfile(args.config_file) != True:
    print("config file do not exist or is not a file.")
    sys.exit(1)

# Import config file.
config = configparser.ConfigParser()
conf_file = args.config_file
config.read(conf_file)
    
# Configure logging.
logging.basicConfig(filename=config["logging"]["logfile"], format='%(asctime)s: %(levelname)s: %(message)s', level=logging.INFO)


# Take backup of folders.
def backup_folders(tar_bin, folders_to_backup, dst_folder):
    # Take backup of folders in folders_to_backup.
    for folder in folders_to_backup:
        # Create tar archive name.
        backup_name = folder.replace("/","_") + ".tar.gz"

        try:
            output = subprocess.run([tar_bin,"-czf", dst_folder + "/" + backup_name, folder], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if output.returncode != 0:
                logging.error("returncode of cmd tar is non zero")
        except subprocess.CalledProcessError as e:
            logging.error("returncode of cmd tar is non zero")
        except:
            logging.error("unkonwn exception running subprocess with tar")


# Take backup of mariadb databases.
def backup_mariadb(mariadbdump_bin, mariadb_root_password, dst_folder):
    try:
        f = open(dst_folder + "/" + "full_db_dump.sql","w")
        output = subprocess.run([mariadbdump_bin,"-h","localhost","--all-databases","-uroot","-p" + mariadb_root_password], check=True, stdout = f)
        if output.returncode != 0:
            logging.error("returncode of cmd mysqldump is none zero")
    except subprocess.CalledProcessError as e:
        logging.error("returncode of cmd mysqldump is none zero")
    except:
        logging.error("unknown exception running subprocess with mysqldump")


# Clear/remove old backups.
def clear_backups(save_backups_to, days_to_save_backups):
    # Check if save_backups_to is a folder.
    if not os.path.exists(save_backups_to): 
        logging.error("can not find folder where backups are saved")

    # Get list of all files only in the given directory.
    list_of_files = filter(os.path.isfile, glob.glob(save_backups_to + '/*.zip'))

    # Sort list of files based on last modification time in ascending order.
    list_of_files = sorted(list_of_files, key = os.path.getmtime)

    # If we have less or equal of 7 backups then exit.
    if len(list_of_files) <= days_to_save_backups:
        logging.info("we have less then or equals to 7 backups saved, exit without deleting anything")
        sys.exit(0)

    list_of_files.reverse()
    count = 0

    # Only save days_to_save_backups days of backups, remove other.
    for file in list_of_files:
        count = count + 1
        if count <= days_to_save_backups:
            continue
        else:
            os.remove(file)
            logging.info("removing: " + save_backups_to + "/" + file)


# sha256 checksum of file.
def sha256_of_file(file):
    # 65kb
    buf_size = 65536

    sha256 = hashlib.sha256()

    with open(file, 'rb') as f:
        while True:
            data = f.read(buf_size)
            if not data:
                break
            sha256.update(data)

    return sha256.hexdigest()
        

# Encrypt file src_file with gpg using pubkey with fingerprint pubkey_fingerprint and save the encrypted file to dst_folder adding .gpg to src_filename.
def gpg_encrypt(pubkey_fingerprint, src_file, src_filename, dst_folder):
    gpg = gnupg.GPG()
    gpg.encoding = 'utf-8'

    stream = open(src_file, 'rb')
    encrypted_data = gpg.encrypt_file(stream, pubkey_fingerprint, armor = False, always_trust = True)
    
    # Encryption has failed.
    if encrypted_data.ok != True:
        logging.error("gpg encryption failed with message " + str(encrypted_data.status))
        return False

    encrypted_file = dst_folder + "/" + src_filename + ".gpg"

    with open(encrypted_file, "wb") as file:
        file.write(encrypted_data.data)

    logging.info("successfully encrypted backup")
    return True


# Send backup to backup_receiver, backup_receiver should be located at different DC and location.
def send_to_backup_receiver(backup_path, filename, url, password):
    # Get the sha256 checksum of file.
    sha256 = sha256_of_file(backup_path)

    files = {"file": open(backup_path,"rb")}
    data = {
            "filename": filename,
            "password": password,
            "sha256": sha256
            }

    # Send backup to backup_receiver
    r = requests.post(url, files=files, data=data, timeout=10)

    # Log result.
    if str(r.status_code) == "200" and r.text == "done":
        logging.info("successfully sent backup to backup_receiver")
    else:
        logging.error("failed to sent backup to backup_receiver")


if __name__ == "__main__":
    logging.info("starting backup job")

    # Working folder.
    tmp_folder = config["DEFAULT"]["tmp_folder"]

    # Backups will be saved to this folder.
    save_backups_to = config["DEFAULT"]["save_backups_to"]

    # The folder to take backups on.
    folders_to_backup = str.split(config["DEFAULT"]["folders_to_backup"])

    # Tar binary location.
    tar_bin = config["DEFAULT"]["tar_bin"]

    # Mariadb-dump binary location.
    mariadbdump_bin = config["DEFAULT"]["mariadbdump_bin"]

    # Mariadb root password.
    mariadb_root_password = config["mariadb"]["root_password"]

    # Number of days to save backups.
    days_to_save_backups = int(config["DEFAULT"]["days_to_save_backups"])

    # Create tmp folder.
    if not os.path.exists(tmp_folder): 
        os.makedirs(tmp_folder) 

    # Create folder to save backups to.
    if not os.path.exists(save_backups_to): 
        os.makedirs(save_backups_to)

    # Create tmp folder for todays date.
    today = str(datetime.date.today())
    if not os.path.exists(tmp_folder + "/" + today): 
        os.makedirs(tmp_folder + "/" + today)
    tmp_folder_date = tmp_folder + "/" + today

    # Take backup of folers.
    backup_folders(tar_bin, folders_to_backup, tmp_folder_date)
    
    # Take backup of mariadb all databases.
    if config["mariadb"].getboolean("take_backup") == True:
        backup_mariadb(mariadbdump_bin, mariadb_root_password, tmp_folder_date)

    # Compress all files with zip.
    hostname = platform.uname().node
    backup_filename = "backup." + hostname + "." + today + ".zip"
    backup_path = save_backups_to + "/" + backup_filename
    shutil.make_archive(backup_path.replace(".zip",""), 'zip', tmp_folder_date)

    # Change premissions on backupsfile.
    os.chmod(backup_path, 0o640)

    # Remove content in tmp folder.
    shutil.rmtree(tmp_folder_date)
    
    # Encrypt backup with openPGP public key.
    if config["gpg_encryption"]["use"] == "Yes":
        pubkey_fingerprint = config["gpg_encryption"]["pubkey_fingerprint"]

        status = gpg_encrypt(pubkey_fingerprint, backup_path, backup_filename, save_backups_to)

        # If encryption fails program will exit with 1.
        if status != True:
            sys.exit(1)

        # Remove unencrypted backup.
        os.remove(backup_path)

        # Set vars that should contain the backups full path and filename to the encrypted backup file.
        backup_filename = backup_filename + ".gpg"
        backup_path = save_backups_to + "/" + backup_filename

    # Send backups to backup_receiver.
    if config["backup_receiver"]["use"] == "Yes":
        url = config["backup_receiver"]["url"]
        password = config["backup_receiver"]["password"]

        send_to_backup_receiver(backup_path, backup_filename, url, password)

    # Remove old backups.
    clear_backups(save_backups_to, days_to_save_backups)
    
    logging.info("backup job finished succesfully")