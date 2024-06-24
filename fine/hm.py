import os
import time
import subprocess
import logging
from configparser import ConfigParser, NoSectionError, NoOptionError
from twocaptcha import TwoCaptcha
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from seleniumbase import SB
from PIL import Image
from weasyprint import HTML
from urllib3.exceptions import ProtocolError

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[
    logging.FileHandler("crash_report.log"),
    logging.StreamHandler()
])

def read_config():
    config = ConfigParser()
    try:
        config.read('config.ini')
        logging.info("Config file read successfully.")
        return config
    except Exception as e:
        logging.error(f"Failed to read config file: {e}")
        raise

def validate_date_format(date_str):
    try:
        time.strptime(date_str, '%m-%d-%Y')
        return True
    except ValueError:
        return False

def fill_in_search_criteria_new_site(driver, rd_number, date_of_crash):
    try:
        driver.find_element(By.ID, "SearchOption").click()
        rd_number_field = driver.find_element(By.ID, "SearchByRdNumberData_rd")
        date_of_crash_field = driver.find_element(By.ID, "SearchByRdNumberData_cd")
        rd_number_field.clear()
        rd_number_field.send_keys(rd_number)
        date_of_crash_field.clear()
        date_of_crash_field.send_keys(date_of_crash)
    except NoSuchElementException as e:
        logging.error(f"Error in filling search criteria for new site: {e}")
        raise

def fill_in_search_criteria_old_site(driver, rd_number, date_of_crash):
    try:
        rd_number_field = driver.find_element(By.ID, "rd")
        date_of_crash_field = driver.find_element(By.ID, "crashDate")
        rd_number_field.clear()
        rd_number_field.send_keys(rd_number)
        date_of_crash_field.clear()
        date_of_crash_field.send_keys(date_of_crash)
    except NoSuchElementException as e:
        logging.error(f"Error in filling search criteria for old site: {e}")
        raise

def solve_recaptcha_twocaptcha(api_key, site_key, url, max_retries=3):
    solver = TwoCaptcha(api_key)
    retries = 0
    while retries < max_retries:
        try:
            result = solver.recaptcha(sitekey=site_key, url=url)
            return result['code']
        except (Exception, ProtocolError, ConnectionError) as e:
            logging.error(f"Error solving reCAPTCHA: {e}")
            retries += 1
            if retries < max_retries:
                logging.info(f"Retrying reCAPTCHA solving... ({retries}/{max_retries})")
                time.sleep(5)  # Wait before retrying
            else:
                raise

def take_full_page_screenshot(driver, file_path):
    try:
        total_height = driver.execute_script("return document.body.scrollHeight")
        driver.set_window_size(1920, total_height)
        time.sleep(2)
        driver.save_screenshot(file_path)
        return file_path
    except WebDriverException as e:
        logging.error(f"Error taking screenshot: {e}")
        raise

def convert_html_to_pdf(html_content, pdf_path):
    try:
        HTML(string=html_content).write_pdf(pdf_path)
        logging.info(f"HTML content converted to PDF: {pdf_path}")
        return pdf_path
    except Exception as e:
        logging.error(f"Error converting HTML to PDF: {e}")
        return None

def lookup_crash_info_new_site(config, rd_prefix, rd_number_start, rd_number_end, date_of_crash, success_rd_numbers_file, timeout_rd_numbers_file):
    successful_rd_numbers = []
    unsuccessful_rd_numbers = []
    timeout_rd_numbers = []
    last_checked_time = time.time()
    session_retry_attempts = 0

    for rd_number in range(rd_number_start, rd_number_end + 1):
        rd_number_str = f"{rd_prefix}{rd_number:06}"
        while session_retry_attempts < 3:
            try:
                with SB(uc=True) as sb:
                    url = config.get('URLs', 'new_site')
                    time.sleep(5)  # Delay before opening the website
                    sb.driver.uc_open_with_reconnect(url, 6)

                    try:
                        WebDriverWait(sb.driver, 60).until(EC.presence_of_element_located((By.ID, "SearchOption")))
                        fill_in_search_criteria_new_site(sb.driver, rd_number_str, date_of_crash)

                        captcha_response = solve_recaptcha_twocaptcha(config.get('API', '2captcha_api_key'), config.get('Recaptcha', 'new_site_key'), sb.driver.current_url)
                        sb.execute_script(f'document.getElementById("g-recaptcha-response").innerHTML="{captcha_response}";')
                        time.sleep(5)
                        sb.driver.find_element(By.CSS_SELECTOR, "button[name='btnSubmit']").click()

                        try:
                            WebDriverWait(sb.driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".alert-danger")))
                            error_message = sb.driver.find_element(By.CSS_SELECTOR, ".alert-danger").text
                            if "No crash report could be found" in error_message:
                                logging.info(f"No crash report found for RD {rd_number_str}.")
                                unsuccessful_rd_numbers.append(rd_number_str)
                                break
                        except TimeoutException:
                            pass

                        WebDriverWait(sb.driver, 60).until(EC.title_contains("Purchase - Traffic Crash Reports"))
                        successful_rd_numbers.append(rd_number_str)
                        logging.info(f"RD Number {rd_number_str} is successful.")
                        with open(success_rd_numbers_file, 'a') as file:
                            file.write(f"{rd_number_str}\n")

                        last_checked_time = time.time()
                        session_retry_attempts = 0
                        break

                    except TimeoutException:
                        logging.error(f"Timed out waiting for elements to load for RD {rd_number_str}.")
                        timeout_rd_numbers.append(rd_number_str)
                        with open(timeout_rd_numbers_file, 'a') as file:
                            file.write(f"{rd_number_str}\n")
                        break
                    except NoSuchElementException as e:
                        logging.error(f"Element not found for RD {rd_number_str}: {e}")
                        break
                    except WebDriverException as e:
                        logging.error(f"WebDriver exception for RD {rd_number_str}: {e}")
                        if "invalid session id" in str(e):
                            logging.info(f"Reopening browser for RD {rd_number_str} due to session issue.")
                            session_retry_attempts += 1
                            time.sleep(10)
                            continue  # Retry the current RD number
                        else:
                            break
                    except Exception as e:
                        logging.error(f"An unexpected error occurred for RD {rd_number_str}: {e}")
                        break

                # Check if more than 3 minutes have passed since the last successful check
                if time.time() - last_checked_time > 180:
                    logging.info(f"No new RD number checked in the last 3 minutes, retrying...")
                    break  # Exit the loop to retry

            except WebDriverException as e:
                logging.error(f"WebDriver exception occurred: {e}")
                if "invalid session id" in str(e):
                    logging.info(f"Reopening browser for RD {rd_number_str} due to session issue.")
                    session_retry_attempts += 1
                    time.sleep(10)
                    continue  # Retry the current RD number
                else:
                    break

    return successful_rd_numbers, unsuccessful_rd_numbers, timeout_rd_numbers

def lookup_crash_info_old_site(config, successful_rd_numbers, date_of_crash, timeout_rd_numbers_file):
    timeout_rd_numbers = []
    for rd_number_str in successful_rd_numbers:
        with SB(uc=True) as sb:
            url = config.get('URLs', 'old_site')
            time.sleep(5)  # Delay before opening the website
            sb.driver.uc_open_with_reconnect(url, 6)

            try:
                WebDriverWait(sb.driver, 60).until(EC.presence_of_element_located((By.ID, "rd")))
                fill_in_search_criteria_old_site(sb.driver, rd_number_str, date_of_crash)

                captcha_response = solve_recaptcha_twocaptcha(config.get('API', '2captcha_api_key'), config.get('Recaptcha', 'old_site_key'), sb.driver.current_url)
                sb.execute_script(f'document.getElementById("g-recaptcha-response").innerHTML="{captcha_response}";')
                time.sleep(5)
                sb.driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()

                # Convert the entire page to HTML then save as PDF
                html_content = sb.driver.page_source
                pdf_filename = f"{rd_number_str}_{date_of_crash}.pdf"
                pdf_path = convert_html_to_pdf(html_content, os.path.join(os.path.dirname(os.path.abspath(__file__)), pdf_filename))

                if pdf_path:
                    subprocess.run(["start", "", pdf_path], shell=True)

                logging.info(f"Waiting for 30 seconds before processing the next RD number...")
                time.sleep(30)

            except TimeoutException:
                logging.error(f"Timed out waiting for elements to load for RD {rd_number_str}.")
                timeout_rd_numbers.append(rd_number_str)
                with open(timeout_rd_numbers_file, 'a') as file:
                    file.write(f"{rd_number_str}\n")
            except NoSuchElementException as e:
                logging.error(f"Element not found for RD {rd_number_str}: {e}")
            except Exception as e:
                logging.error(f"An unexpected error occurred for RD {rd_number_str}: {e}")

    return timeout_rd_numbers

def main():
    try:
        config = read_config()
    except Exception as e:
        logging.error(f"Error reading config file: {e}")
        return

    rd_prefix = input("Enter the RD number prefix (e.g., JG): ")
    rd_number_start = int(input("Enter RD number start: "))
    rd_number_end = int(input("Enter RD number end: "))
    date_of_crash = input("Enter date (mm-dd-yyyy): ")
    
    if not validate_date_format(date_of_crash):
        logging.error("Invalid date format. Please enter date in mm-dd-yyyy format.")
        return

    success_rd_numbers_file = "successful_rd_numbers.txt"
    timeout_rd_numbers_file = "timeout_rd_numbers.txt"

    while True:
        successful_rd_numbers, unsuccessful_rd_numbers, new_site_timeout_rd_numbers = lookup_crash_info_new_site(
            config, rd_prefix, rd_number_start, rd_number_end, date_of_crash, success_rd_numbers_file, timeout_rd_numbers_file)

        logging.info(f"Retrying {len(new_site_timeout_rd_numbers)} RD numbers that timed out on the new site.")
        successful_rd_numbers.extend(new_site_timeout_rd_numbers)
        
        old_site_timeout_rd_numbers = lookup_crash_info_old_site(config, successful_rd_numbers, date_of_crash, timeout_rd_numbers_file)

        logging.info(f"Old site timed out for {len(old_site_timeout_rd_numbers)} RD numbers. Retrying those.")
        
        if not old_site_timeout_rd_numbers:
            logging.info("All RD numbers processed successfully.")
            break

if __name__ == "__main__":
    main()
