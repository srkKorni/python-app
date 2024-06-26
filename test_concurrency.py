import platform
import subprocess
import os
import sys
import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import pydicom
import urllib3
import shutil
import logging
from pathlib import Path
from collections import defaultdict
from threading import Timer
from concurrent.futures import ThreadPoolExecutor

requests.urllib3.disable_warnings(category=urllib3.exceptions.InsecureRequestWarning)

print(platform.system())

class FileHandler(FileSystemEventHandler):
    def __init__(self, output_dir, api_url, token, client_id, branch_id, user_id, max_workers=4):
        self.output_dir = output_dir
        self.api_url = api_url
        self.token = token
        self.client_id = client_id
        self.branch_id = branch_id
        self.user_id = user_id
        self.study_files = defaultdict(list)
        self.upload_timer = {}
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.dcm'):
            self.handle_file(event.src_path)

    def handle_file(self, file_path):
        try:
            #
            # Read the DICOM file
            dicom_data = pydicom.dcmread(file_path)
            study_instance_uid = dicom_data.StudyInstanceUID
            
            # Add file to the list of files for this study
            self.study_files[study_instance_uid].append(file_path)

            # Schedule the upload of the study after a delay to ensure all files are received
            if study_instance_uid in self.upload_timer:
                self.upload_timer[study_instance_uid].cancel()

            self.upload_timer[study_instance_uid] = Timer(60.0, self.executor.submit, [self.process_study, study_instance_uid])
            self.upload_timer[study_instance_uid].start()

        except Exception as e:
            print(f"Error handling file: {file_path}, Error: {e}")

    def process_study(self, study_instance_uid):
        try:
            study_files = self.study_files.pop(study_instance_uid, [])
            if not study_files:
                return

            # Create directory for StudyInstanceUID if it doesn't exist
            study_dir = os.path.join(self.output_dir, study_instance_uid)
            if not os.path.exists(study_dir):
                os.makedirs(study_dir)

            # Move files to the appropriate directory
            for file_path in study_files:
                new_file_path = os.path.join(study_dir, os.path.basename(file_path))
                os.rename(file_path, new_file_path)

            # Create a zip file for the study
            zip_file_path = os.path.join(self.output_dir, f"{study_instance_uid}.zip")
            shutil.make_archive(zip_file_path.replace('.zip', ''), 'zip', study_dir)

            # Send the zip file to the server
            self.send_file(zip_file_path)
        except Exception as e:
            print(f"Error processing study: {study_instance_uid}, Error: {e}")

    def send_file(self, zip_file_path):
        try:
            headers = {
                'Authorization': 'Basic T3J0aGFuYzpPcnRoYW5jQDEyMzQ='
            }
            with open(zip_file_path, 'rb') as f:
                response = requests.post(self.api_url, headers=headers, data=f, verify=False)
                print(response.text)
            if response.status_code == 200:
                print(f"Successfully sent file: {zip_file_path}")
                self.upload(response.json())
                os.remove(Path(zip_file_path))
                folder = zip_file_path.replace(".zip", "")
                shutil.rmtree(Path(folder))
                print("Files uploaded successfully for create_patient and create patient report")
            else:
                print(f"Failed to send file: {zip_file_path}, Status code: {response.status_code}, {response.json()}")
    
        except Exception as e:
            print(f"Error sending file: {zip_file_path}, Error: {e}")
    
    def upload(self, orthanc_response):
        try:
            study_id = orthanc_response[0]['ParentStudy']
            api_url = f"https://pacs.smaro.app/orthanc/studies/{study_id}"
            headers = {
                'Authorization': 'Basic T3J0aGFuYzpPcnRoYW5jQDEyMzQ='
            }
            response = requests.get(api_url, headers=headers, verify=False)
            if response.status_code == 200:
                print("got the patient level information successfully")
                gender = response.json()['PatientMainDicomTags']['PatientSex']
                dob = response.json()['PatientMainDicomTags']['PatientBirthDate']
                patient_name = response.json()['PatientMainDicomTags']['PatientName']
                patient_id = response.json()['PatientMainDicomTags']['PatientID']
                instance_id = response.json()['MainDicomTags']['StudyInstanceUID']
                response_create_patient = self.create_patient(gender, dob, patient_name, patient_id)
                pat_id = response_create_patient.get("patient_id")
                response_create_patient_report = self.create_patient_report(pat_id, instance_id, study_id)
            else:
                print(f"Failed to get the patient level information, Status code: {response.status_code}, {response.json()}")
        except Exception as e:
            print(f"Error uploading file Error: {e}")

    def create_patient(self, gender, dob, patient_name, patient_id):
        try:
            patient_data = {
                "ref_Code": patient_id,
                "patient_name": patient_name,
                "patient_mobile": "",
                "patient_email": "",
                "dob": dob,
                "gender": gender,
                "address": "",
                "client_id": self.client_id,
                "branch_id": self.branch_id,
                "estatus": 1,
            }
            headers = {
                'Content-Type': 'application/json',
                'token': self.token
            }
            response = requests.post('https://dev-api.smaro.app/api/app/patient/create', 
                                     json=patient_data, headers=headers, verify=False)
            print(response.json())
            print(response.json()['data']['insertId'])
            if response.status_code == 200:
                print("create patient successful")
                return {"success": True, "patient_id": response.json()['data']['insertId']}
            elif response.status_code == 409:
                print("create patient successful")
                return {"success": True, "patient_id": response.json()['data']['insertId']}
            else:
                print(f"Failed to upload create patient, Status code: {response.status_code}, {response.json()}")
                return {"success": False, "patient_id": ""}
        except Exception as e:
            print(f"Error uploading create patient. Error: {e}")
            return {"success": False, "patient_id": ""}

    def create_patient_report(self, patient_id, instance_id, study_id):
        try:
            report_data = {
                "patient_id": patient_id,
                "patient_study_id": study_id,
                "patient_study_instance_id": instance_id,
                "test_type_id": 0,
                "test_sub_type_id": 0,
                "priority_type": "normal",
                "techniques": "",
                "clinical_history": "",
                "clinical_history_file": None,
                "results_type": "",
                "doctor_id": 0,
                "hospital_id": 0,
                "client_login_id": self.user_id,
                "radiologist_id": 0,
                "client_id": self.client_id,
                "branch_id": self.branch_id
            }
            headers = {
                'Content-Type': 'application/json',
                'token': self.token
            }

            response = requests.post('https://dev-api.smaro.app/api/diagnostics/patient/report/create', 
                                     json=report_data, headers=headers, verify=False)
            if response.status_code == 200:
                print("create patient report successful")
                return {"success": True}
            else:
                print(f"Failed to upload create patient report, Status code: {response.status_code}, {response.json()}")
                return {"success": False}
        except Exception as e:
            print(f"Error uploading create patient report. Error: {e}")
            return {"success": False}

def start_storescp(port, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    os.environ['DCMDICTPATH'] = 'dcmtk/share/dcmtk-3.6.8/dicom.dic'  # Replace with the actual path to dicom.dic
    command = [
        "./dcmtk/bin/storescp",
        str(port),
        "--filename-extension",
        ".dcm",
        "--output-directory",
        output_dir,
        "--max-pdu",
        "46726",
        "+xa",
        "+B",
    ]

    storescp_process = subprocess.Popen(command)
    print(f"storescp started with PID: {storescp_process.pid}")
    return storescp_process

def stop_storescp(storescp_process):
    storescp_process.terminate()
    storescp_process.wait()
    print("storescp stopped.")

def start_monitoring(output_dir, api_url, token, client_id, branch_id, user_id):
    event_handler = FileHandler(output_dir, api_url, token, client_id, branch_id, user_id)
    observer = Observer()
    observer.schedule(event_handler, path=output_dir, recursive=True)
    observer.start()
    print(f"Started monitoring {output_dir}")

    try:
        while True:
            pass
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

# Example usage
if __name__ == "__main__":
    token = sys.argv[1]
    client_id = sys.argv[2]
    branch_id = sys.argv[3]
    user_id = sys.argv[4]
    port = 11114  # Port to listen on
    output_dir = './output/'  # Directory to store received DICOM files
    api_url = "https://pacs.smaro.app/orthanc/instances"  # Replace with your API endpoint
    storescp_process = start_storescp(port, output_dir)
    print("storescp started. Press Ctrl+C to stop.")
    start_monitoring(output_dir, api_url, token, client_id, branch_id, user_id)
    stop_storescp(storescp_process)
