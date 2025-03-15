import re
import requests

def extract_emails(url):
    # Fetch the HTML content
    response = requests.get(url)
    html_content = response.text
    
    # Define the regular expression for email addresses
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    
    # Find all email addresses in the HTML content
    emails = re.findall(email_pattern, html_content)
    
    return emails

if __name__ == "__main__":
    url = input("Enter the URL of the HTML page: ")
    emails = extract_emails(url)
    print("Extracted emails:")
    for email in emails:
        print(email)
