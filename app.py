from fileinput import isfirstline
from flask import Flask, request, jsonify, render_template
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from flask_cors import CORS
import logging
import threading
import time
import os
import re
import pdfplumber
import google.generativeai as genai
import sqlite3
from dotenv import load_dotenv



# Initialize Gemini API
genai.configure(api_key="AIzaSyDtGhH9UJnQPB_Yccv5aaEe1C8rZuDx9T0")

app = Flask(__name__, template_folder="templates")
CORS(app, supports_credentials=True)

# Initialize logging
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DATABASE = "jobsease.db"
APPLICATIONS_DB = "applications.db"

def init_db():
    with sqlite3.connect(APPLICATIONS_DB) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                job_role TEXT,
                experience INTEGER,
                location_type TEXT,
                location TEXT
            )
        ''')
        # Add resume_details table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS resume_details (
                email TEXT PRIMARY KEY,
                phone TEXT,
                linkedin TEXT,
                resume_text TEXT
            )
        ''')
        conn.commit()
    log.info("✅ Database initialized successfully.")

def connect_db():
    return sqlite3.connect(APPLICATIONS_DB)

def store_resume_details(details):
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO resume_details (email, phone, linkedin, resume_text)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET 
                    phone = excluded.phone, 
                    linkedin = excluded.linkedin, 
                    resume_text = excluded.resume_text
            ''', (details["email"], details["phone"], details["linkedin"], details["resume_text"]))
            conn.commit()
    except Exception as e:
        log.error(f"Error storing resume details: {e}")

def extract_resume_details(pdf_path):
    details = {"name": "", "email": "", "phone": "", "linkedin": "", "resume_text": ""}
    try:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        with pdfplumber.open(pdf_path) as pdf:
            text = "\n".join([page.extract_text() or "" for page in pdf.pages])

        details["resume_text"] = text
        # Improved regex patterns
        email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        phone_match = re.search(r"\+?\d{1,3}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text)
        linkedin_match = re.search(r"(https?:\/\/)?(www\.)?linkedin\.com\/in\/[a-zA-Z0-9_-]+\/?", text)

        details["email"] = email_match.group() if email_match else "Not found"
        details["phone"] = phone_match.group() if phone_match else "Not found"
        details["linkedin"] = linkedin_match.group() if linkedin_match else "Not found"

        log.info(f"🔍 Extracted Resume Details: {details}")
        store_resume_details(details)
        return details, text

    except FileNotFoundError as e:
        log.error(f"❌ File not found: {e}")
        return details, ""
    except Exception as e:
        log.error(f"❌ Error extracting resume details: {e}")
        return details, ""

init_db()

def setup_logger():
    dt = time.strftime("%Y_%m_%d_%H_%M_%S")
    os.makedirs('./logs', exist_ok=True)

    logging.basicConfig(
        filename=f'./logs/{dt}_JobEase.log',
        filemode='w',
        format='%(asctime)s::%(name)s::%(levelname)s::%(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.DEBUG
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S')
    console_handler.setFormatter(console_format)
    logging.getLogger().addHandler(console_handler)

setup_logger()

UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Ensure the uploads directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

class JobEaseBot:
    def __init__(self, username, password, job_role, work_type, location, experience, resume_path, expected_salary="100000", requires_sponsorship="No"):
        log.info("Initializing JobEase Bot...")
        self.username = username
        self.password = password
        self.job_role = job_role
        self.work_type = work_type
        self.location = location
        self.experience = int(experience) if experience else 0
        self.resume_path = resume_path
        self.expected_salary = expected_salary
        self.requires_sponsorship = requires_sponsorship
        self.resume_details, self.resume_text = extract_resume_details(resume_path)

        log.info(f"Extracted Resume Details: {self.resume_details}")
        self.blacklist = ["unpaid"]
        self.browser = self.setup_browser()
        self.wait = WebDriverWait(self.browser, 120)

    def setup_browser(self):
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-features=WebRTC")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")
        browser = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
        return browser

    def login_to_linkedin(self):
        log.info("Opening LinkedIn login page...")
        self.browser.get("https://www.linkedin.com/login")
        try:
            username_field = self.browser.find_element(By.ID, "username")
            password_field = self.browser.find_element(By.ID, "password")
            login_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@type="submit"]')))
            username_field.send_keys(self.username)
            password_field.send_keys(self.password)
            login_button.click()

            log.info("Waiting for user to complete login...")
            for _ in range(180):
                if "feed" in self.browser.current_url:
                    log.info("Login successful.")
                    return True
                time.sleep(1)
            log.error("Login timeout.")
            return False
        except Exception as e:
            log.error(f"Login failed: {e}")
            return False

    def search_jobs(self):
        log.info(f"🔍 Searching jobs for {self.job_role} in {self.location}...")
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={self.job_role}&location={self.location}&f_E={self.experience}"
        for attempt in range(3):
            try:
                log.info(f"Attempt {attempt + 1}: Navigating to {search_url}")
                self.browser.get(search_url)
                time.sleep(5)
                self.browser.save_screenshot(f"debug_screenshot_{attempt}.png")
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.jobs-search-results__list-item')))
                jobs = self.scrape_job_listings()
                if not jobs:
                    log.warning("⚠ No jobs found. The LinkedIn UI might have changed.")
                return jobs
            except Exception as e:
                log.error(f"Error on attempt {attempt + 1}: {e}")
                if attempt == 2:
                    return []
                time.sleep(5)

    def get_job_description(self):
        try:
            job_desc_element = self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.jobs-description-content')))
            return job_desc_element.text.strip()
        except Exception as e:
            log.error(f"Error extracting job description: {e}")
            return ""

    def generate_cover_letter(self, job_description):
        log.info("Generating cover letter...")
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"""Write a professional cover letter for the following job description:\n{job_description}
        Based on the applicant's resume: {self.resume_text}"""
        response = model.generate_content(prompt)
        cover_letter_text = response.text.strip() if response.text else "Dear Hiring Manager, I am excited to apply for this position."
        cover_letter_path = os.path.join("uploads", f"cover_letter_{int(time.time())}.txt")
        with open(cover_letter_path, "w", encoding="utf-8") as f:
            f.write(cover_letter_text)
        log.info(f"Cover letter saved at: {cover_letter_path}")
        return cover_letter_path

    def customize_resume(self, job_description):
        log.info("Customizing resume with Gemini AI...")
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"""Rewrite the following resume to better match this job description:
        Job Description: {job_description}
        Resume: {self.resume_text}"""
        response = model.generate_content(prompt)
        return response.text.strip() if response.text else self.resume_text

    def scrape_job_listings(self):
        log.info("Scraping job listings...")
        jobs = []
        try:
            self.wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, '.jobs-search-results__list-item')))
            for _ in range(5):
                self.browser.execute_script("window.scrollBy(0, 1000);")
                time.sleep(1)
            job_cards = self.browser.find_elements(By.CSS_SELECTOR, '.jobs-search-results__list-item')
            for card in job_cards:
                try:
                    title = card.find_element(By.CSS_SELECTOR, '.job-card-list__title').text
                    company = card.find_element(By.CSS_SELECTOR, '.job-card-container__company-name').text
                    location = card.find_element(By.CSS_SELECTOR, '.job-card-container__metadata-item').text
                    if any(word in title.lower() or word in company.lower() for word in self.blacklist):
                        continue
                    jobs.append({'title': title, 'company': company, 'location': location})
                except Exception:
                    continue
            log.info(f"Found {len(jobs)} jobs.")
        except Exception as e:
            log.error(f"Error scraping jobs: {e}")
        return jobs

    def apply_to_job(self, job_url):
        max_retries = 3
        for attempt in range(max_retries):
            log.info(f"Applying to job: {job_url} (Attempt {attempt + 1}/{max_retries})")
            self.browser.get(job_url)
            time.sleep(2)  # Rate limiting delay
            try:
                easy_apply_button = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[aria-label="Easy Apply"]')))
                easy_apply_button.click()
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'form.jobs-easy-apply-form')))
                time.sleep(2)
                job_description = self.get_job_description()
                cover_letter_path = self.generate_cover_letter(job_description)
                self.fill_form()
                self.handle_custom_questions()
                self.upload_cover_letter(cover_letter_path)
                next_buttons = self.browser.find_elements(By.CSS_SELECTOR, 'button[aria-label="Next"]')
                for btn in next_buttons:
                    try:
                        btn.click()
                        time.sleep(1)
                    except Exception as e:
                        log.warning(f"Could not click Next button: {e}")
                submit_button = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[aria-label="Submit application"]')))
                submit_button.click()
                log.info("✅ Application submitted successfully.")
                return True
            except Exception as e:
                log.error(f"❌ Error applying to job (Attempt {attempt + 1}/{max_retries}): {e}")
            log.warning(f"Retrying application ({attempt + 1}/{max_retries})")
            time.sleep(2)
        log.error(f"❌ Failed to apply after {max_retries} attempts.")
        return False

    def fill_form(self):
        try:
            self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'form.jobs-easy-apply-form')))
            time.sleep(2)

            def try_fill_field(selector, value, field_name):
                for attempt in range(3):
                    try:
                        field = self.browser.find_element(By.CSS_SELECTOR, selector)
                        field.clear()
                        field.send_keys(value)
                        time.sleep(1)
                        return
                    except Exception as e:
                        log.warning(f"{field_name} field not found on attempt {attempt+1}: {e}")
                        time.sleep(2)
                log.error(f"❌ {field_name} field could not be found after 3 attempts.")

            try_fill_field('input[aria-label="Email"]', self.resume_details["email"], "Email")
            try_fill_field('input[aria-label="Phone"]', self.resume_details["phone"], "Phone")

            try:
                resume_field = self.browser.find_element(By.CSS_SELECTOR, 'input[type="file"]')
                resume_field.send_keys(self.resume_path)
                time.sleep(1)
            except Exception as e:
                log.warning(f"Resume upload field not found: {e}")

            log.info("✅ Filled application form successfully.")
        except Exception as e:
            log.error(f"❌ Error filling form: {e}")

    def handle_custom_questions(self):
        try:
            questions = self.browser.find_elements(By.CSS_SELECTOR, '.jobs-easy-apply-form-section__grouping')
            for question in questions:
                label = question.find_element(By.CSS_SELECTOR, 'label').text
                answer_field = question.find_element(By.CSS_SELECTOR, 'input, textarea')

                if "Why do you want this job?" in label:
                    answer_field.send_keys("I am excited about this opportunity because my skills align with your job requirements.")
                elif "Do you require sponsorship?" in label:
                    answer_field.send_keys(self.requires_sponsorship)
                elif "Expected Salary" in label:
                    answer_field.send_keys(self.expected_salary)

                log.info(f"Answered question: {label}")
        except Exception as e:
            log.error(f"Error handling custom questions: {e}")

    def upload_cover_letter(self, cover_letter_path):
        if not cover_letter_path or not os.path.exists(cover_letter_path):
            log.info("No cover letter uploaded.")
            return
        try:
            cover_letter_field = self.browser.find_element(By.CSS_SELECTOR, 'input[type="file"]')
            cover_letter_field.send_keys(cover_letter_path)
            log.info("Cover letter uploaded successfully.")
        except Exception as e:
            log.warning("Cover letter upload not required or field not found.")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/save-application', methods=['POST'])
def save_application():
    try:
        conn = connect_db()
        cursor = conn.cursor()
        username = request.form.get("username")
        job_role = request.form.get("job_role")
        experience = request.form.get("experience")
        location_type = request.form.get("location_type")
        location = request.form.get("location") if location_type == "On-Site" else "N/A"

        log.info(f"Saving Application: {username}, {job_role}, {experience}, {location_type}, {location}")

        cursor.execute(
            "INSERT INTO applications (username, job_role, experience, location_type, location) VALUES (?, ?, ?, ?, ?)",
            (username, job_role, experience, location_type, location)
        )
        conn.commit()
        conn.close()
        return jsonify({"message": "Application saved successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get-applications', methods=['GET'])
def get_applications():
    try:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("SELECT job_role, experience, location_type, location FROM applications")
        applications = [{"job_role": row[0], "experience": row[1], "location_type": row[2], "location": row[3]} for row in cursor.fetchall()]
        conn.close()
        return jsonify({"applications": applications}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/saved-applications', methods=['GET'])
def saved_applications():
    try:
        conn = connect_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, username, job_role, experience, location_type, location
            FROM applications
            ORDER BY id DESC
            LIMIT 10
        """)
        applications = [
            {
                "id": row[0],
                "username": row[1],
                "job_role": row[2],
                "experience": row[3],
                "location_type": row[4],
                "location": row[5]
            }
            for row in cursor.fetchall()
        ]
        conn.close()
        return jsonify({"applications": applications}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/start-bot', methods=['POST'])
def start_bot():
    log.info("🚀 Received request to start bot...")
    if "resume" not in request.files:
        log.error("❌ No resume uploaded.")
        return jsonify({"error": "No resume uploaded"}), 400

    resume_file = request.files["resume"]
    if resume_file.filename == "":
        log.error("❌ No selected file.")
        return jsonify({"error": "No selected file"}), 400

    resume_path = os.path.join(app.config["UPLOAD_FOLDER"], resume_file.filename)
    resume_file.save(resume_path)

    if not os.path.exists(resume_path):
        log.error(f"❌ Resume file not found: {resume_path}")
        return jsonify({"error": "Resume upload failed."}), 500

    log.info(f"📂 Resume file received: {resume_path}")
    extracted_details, _ = extract_resume_details(resume_path)
    log.info(f"🔍 Extracted details: {extracted_details}")

    data = {
        "username": request.form.get("username"),
        "password": request.form.get("password"),
        "job_role": request.form.get("job_role"),
        "work_type": request.form.get("location_type"),
        "experience": request.form.get("experience"),
        "location": request.form.get("location", "worldwide"),
        "resume_path": resume_path,
        "expected_salary": request.form.get("expected_salary", "100000"),
        "requires_sponsorship": request.form.get("requires_sponsorship", "No")
    }

    for key, value in data.items():
        log.info(f"Form data - {key}: {value}")
 
    # Input validation
    if not data["username"] or not data["password"]:
        log.error("Missing username or password")
        return jsonify({"error": "Username and password are required."}), 400
    if not data["job_role"]:
        log.error("Missing job role")
        return jsonify({"error": "Job role is required."}), 400
    try:
        experience = int(data["experience"])
        if experience < 0:
            raise ValueError("Experience cannot be negative.")
    except (ValueError, TypeError) as e:
        log.error(f"Invalid experience: {e}")
        return jsonify({"error": "Experience must be a non-negative integer."}), 400

    bot = None
    try:
        bot = JobEaseBot(
            data["username"], data["password"], data["job_role"],
            data["work_type"], data["location"], data["experience"],
            data["resume_path"], data["expected_salary"], data["requires_sponsorship"]
        )
        log.info("🔑 Logging into LinkedIn...")
        if not bot.login_to_linkedin():
            log.error("❌ LinkedIn login failed.")
            return jsonify({"error": "Login failed. Check credentials or manually complete login."}), 401
        log.info("✅ Successfully logged in to LinkedIn.")

        log.info("🔍 Searching for jobs...")
        jobs = bot.search_jobs()

        if not jobs:
            log.warning("⚠ No jobs found based on the given criteria.")
        log.info(f"📄 Found {len(jobs)} jobs!" if jobs else "❌ No jobs found.")
        return jsonify({
            "jobs": jobs,
            "message": f"Found {len(jobs)} jobs!" if jobs else "No jobs found.",
            "resume_details": bot.resume_details
        })

    except Exception as e:
        log.error(f"❌ Error in bot execution: {str(e)}", exc_info=True)
        return jsonify({"error": f"Bot execution failed: {str(e)}"}), 500

    finally:
        if bot and hasattr(bot, 'browser'):
            log.info("🛑 Closing browser session...")
            bot.browser.quit()
            log.info("✅ Closed the bot browser session.")
        # Clean up temporary files
        for file in os.listdir(UPLOAD_FOLDER):
            if file.startswith("cover_letter_"):
                try:
                    os.remove(os.path.join(UPLOAD_FOLDER, file))
                    log.info(f"🗑️ Removed temporary file: {file}")
                except Exception as e:
                    log.warning(f"Failed to remove temporary file {file}: {e}")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
