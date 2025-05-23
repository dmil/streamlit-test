#!/usr/bin/env python3
"""
Simple Streamlit front-end to display announcements from MongoDB.
Shows title, school, date, URL, and source base URL for each announcement.
"""
import os
from datetime import datetime
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import io


# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

# Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "campus_data")

# Connect to MongoDB
def get_db():
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]


def display_announcements(db):
    """Display the announcements view."""
    st.markdown('⚠️ Please note that this is an unedited **first draft** proof-of-concept. Classifications **WILL BE** inaccurate. ⚠️', unsafe_allow_html=True)

    # Fetch all unique schools from the database
    schools_cursor = db.schools.find({}, {"_id": 0, "name": 1})
    schools = [{"name": school.get("name")} for school in schools_cursor]

    # Create a dropdown for school selection with alphabetically sorted options
    school_names = [school["name"] for school in schools]
    school_names.sort()  # Sort school names alphabetically
    school_options = ["All"] + school_names
    selected_school = st.selectbox("Filter by School", school_options)

    st.markdown('>_Check any box to filter for items identified by our LLM as related to that category.<br/>Hover on each question mark for more information about the criteria._', unsafe_allow_html=True)

    # Create a columns layout for the checkboxes
    col1, col2 = st.columns(2)

    llm_prompt = (
        "Analyze this university announcement and determine if it:\n"
        "1. Is related to the university responding to federal government or federal administration actions\n"
        "2. Mentions a lawsuit or legal action\n"
        "3. Discusses funding cuts or funding issues\n"
        "4. Relates to campus protests or disruptions\n\n"
        "For each category, provide whether it's related (true/false) and if true, a brief reason."
    )

    with col1:
        show_govt_related = st.checkbox("👨‍⚖️ Government Related", 
            help="LLM Prompt: Items where the university is supporting or opposing federal government or administration actions")
        show_lawsuit_related = st.checkbox("⚖️ Lawsuit Related", 
            help="LLM Prompt: Items mentioning lawsuits or legal actions related to the university")        
        
    with col2:
        show_funding_related = st.checkbox("💰 Funding Related", 
            help="LLM Prompt: Items discussing funding cuts or financial issues")
        show_protest_related = st.checkbox("🪧 Protest Related", 
            help="LLM Prompt: Items mentioning campus protests or disruptions")

    # Build the query based on the selected school
    query = {}
    if selected_school != "All":
        query["school"] = selected_school

    # Add filters based on selected categories
    filter_conditions = []
    if show_govt_related:
        filter_conditions.append({"llm_response.government_related.related": True})
    if show_lawsuit_related:
        filter_conditions.append({"llm_response.lawsuit_related.related": True})
    if show_funding_related:
        filter_conditions.append({"llm_response.funding_related.related": True})
    if show_protest_related:
        filter_conditions.append({"llm_response.protest_related.related": True})

    # Combine filters with OR if any are selected
    if filter_conditions:
        query["$or"] = filter_conditions

    # Add date filter for announcements after Jan 1, 2025
    query["date"] = {"$gte": datetime(2025, 1, 1)}

    # Fetch announcements based on the query
    cursor = db.announcements.find(query, {"_id": 0}).sort("date", -1)
    announcements = list(cursor)  # Convert cursor to a list to get its length
    num_announcements = len(announcements)

    st.write(f"Number of announcements: **{num_announcements}** (from Jan 1, 2025 onwards)")
    
    # Add download button for CSV
    if announcements:
        # Create a download button
        csv = convert_to_csv(announcements, db)
        st.download_button(
            label="📥 Download data as CSV",
            data=csv,
            file_name="announcements_data.csv",
            mime="text/csv",
        )

    for ann in announcements:
        title = ann.get("title", "No Title")

        # Format the date
        date_value = ann.get("date")
        if isinstance(date_value, datetime):
            date_str = date_value.strftime("%Y-%m-%d")
        else:
            date_str = str(date_value)

        # Get school name and color
        school_name = ann.get("school", "Unknown School")
        school_color = "#000000"  # Default to black if no color is found
        school_doc = db.schools.find_one({"name": school_name}, {"color": 1})
        if school_doc and school_doc.get("color"):
            school_color = school_doc["color"]

        # Announcement URL
        url = ann.get("url", "")
        # Source base URL (if runner annotated it)
        base_url = ann.get("base_url", "")

        st.subheader(title)
        content = ann.get("content", "")
        announcement_html = f"""
        <p style="margin-bottom: 0.5em;">
            <strong>School:</strong> <span style="background-color:{school_color}; padding:2px 4px; border-radius:4px; color:#ffffff;">{school_name}</span><br>
            <strong>Date:</strong> {date_str}<br>
            <strong>Content Scraped:</strong> {'✅' if content else '👎'}<br>
            <strong>Announcement URL:</strong><br/> <a href="{url}">{url}</a>
        </p>
        """
        st.markdown(announcement_html, unsafe_allow_html=True)

        # LLM Response Section
        if ann.get("llm_response"):
            llm_response = ann.get("llm_response")

            # Check each category and display if related
            categories_found = []

            if llm_response.get("government_related", {}).get("related"):
                categories_found.append(("👨‍⚖️ Government", llm_response["government_related"]["reason"]))

            if llm_response.get("lawsuit_related", {}).get("related"):
                categories_found.append(("⚖️ Lawsuit", llm_response["lawsuit_related"]["reason"]))

            if llm_response.get("funding_related", {}).get("related"):
                categories_found.append(("💰 Funding", llm_response["funding_related"]["reason"]))

            if llm_response.get("protest_related", {}).get("related"):
                categories_found.append(("🪧 Protest", llm_response["protest_related"]["reason"]))

            # Display all found categories
            for category, reason in categories_found:
                st.markdown(f"🤖 **LLM Says ({category}):** {reason}")

        st.markdown("<hr style=\"margin-top:0.5em;margin-bottom:0.5em;\">", unsafe_allow_html=True)


def convert_to_csv(announcements, db):
    """Convert announcements data to CSV format, excluding content column."""
    # Create a list to store processed announcements
    processed_data = []
    
    for ann in announcements:
        # Extract base data
        processed_ann = {
            "title": ann.get("title", ""),
            "school": ann.get("school", ""),
            "date": ann.get("date"),
            "url": ann.get("url", ""),
            # "base_url": ann.get("base_url", "")
        }
        
        # Add LLM response fields if available
        llm_response = ann.get("llm_response", {})
        
        # Add government related info
        govt_data = llm_response.get("government_related", {})
        processed_ann["govt_related"] = govt_data.get("related", False)
        processed_ann["govt_reason"] = govt_data.get("reason", "") if govt_data.get("related") else ""
        
        # Add lawsuit related info
        lawsuit_data = llm_response.get("lawsuit_related", {})
        processed_ann["lawsuit_related"] = lawsuit_data.get("related", False)
        processed_ann["lawsuit_reason"] = lawsuit_data.get("reason", "") if lawsuit_data.get("related") else ""
        
        # Add funding related info
        funding_data = llm_response.get("funding_related", {})
        processed_ann["funding_related"] = funding_data.get("related", False)
        processed_ann["funding_reason"] = funding_data.get("reason", "") if funding_data.get("related") else ""
        
        # Add protest related info
        protest_data = llm_response.get("protest_related", {})
        processed_ann["protest_related"] = protest_data.get("related", False)
        processed_ann["protest_reason"] = protest_data.get("reason", "") if protest_data.get("related") else ""
        
        processed_data.append(processed_ann)
    
    # Convert to pandas DataFrame and then to CSV
    df = pd.DataFrame(processed_data)
    
    # Convert datetime objects to strings
    if 'date' in df.columns:
        df['date'] = df['date'].apply(lambda x: x.strftime('%Y-%m-%d') if isinstance(x, datetime) else str(x))
    
    # Convert to CSV
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    return csv_buffer.getvalue()


def display_scraper_status(db):
    """Display the scraper status tab."""
    st.markdown("### Scraper Status")
    # st.markdown("Overview of all scrapers and their last run times by school.")
    
    # Fetch all schools with scraper information
    schools = list(db.schools.find(
        {"scrapers": {"$exists": True}}, 
        {"name": 1, "color": 1, "scrapers": 1, "last_run": 1}
    ).sort("name", 1))
    
    if not schools:
        st.warning("No scraper information found in the database.")
        return
    
    # Count total scrapers
    total_scrapers = sum(len(school.get("scrapers", [])) for school in schools)
    st.write(f"Total scrapers: **{total_scrapers}** across **{len(schools)}** schools")
    
    # Create an expandable section for each school
    for school in schools:
        school_name = school.get("name", "Unknown School")
        school_color = school.get("color", "#000000")
        scrapers = school.get("scrapers", [])
        
        # Format the last run date
        last_run = school.get("last_run")
        if isinstance(last_run, datetime):
            last_run_str = last_run.strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_run_str = "Never run" if last_run is None else str(last_run)
        
        # Create an expander for each school with the last run time in the title
        expander_title = f"{school_name} - Last run: {last_run_str} - ({len(scrapers)} scrapers)"
        with st.expander(expander_title, expanded=False):
            if not scrapers:
                st.info(f"No scrapers configured for {school_name}")
                continue
            
            # Create a table for the scrapers
            data = []
            for scraper in scrapers:
                data.append({
                    "Name": scraper.get("name", "Unnamed"),
                    "Path": scraper.get("path", "No path"),
                    "URL": scraper.get("url", "No URL")
                })
            
            # Display as a table
            # st.table(pd.DataFrame(data).set_index('Name'))
            # Display as a table
            # st.table(data)
            df = pd.DataFrame(data)
            # # Extract filename from path
            df['Path'] = df['Path'].apply(lambda x: x.split('/')[-1].split('.')[-1])
            st.table(df.set_index('Path'))



def main():
    # Set page config - MUST be the first Streamlit command
    st.set_page_config(
        page_title="Campus Announcements Tracker [DRAFT]",
        page_icon="🎓",
        layout="centered",
        initial_sidebar_state="expanded",
        menu_items={"About": "This is a draft version of the Campus Announcements Tracker."}
    )
    
    st.title("Campus Announcements [DRAFT]")
    st.markdown("Announcements from the provosts' and presidents' offices at select universities.")

    db = get_db()
    
    # Create tabs for different views
    tab1, tab2 = st.tabs(["Announcements", "Scraper Status"])
    
    with tab1:
        display_announcements(db)
        
    with tab2:
        display_scraper_status(db)


if __name__ == "__main__":
    main()
