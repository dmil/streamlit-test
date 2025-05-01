#!/usr/bin/env python3
"""
Simple Streamlit front-end to display announcements from MongoDB.
Shows title, school, date, URL, and source base URL for each announcement.
"""
import os
from datetime import datetime
import streamlit as st
from pymongo import MongoClient

# Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "campus_data")

# Connect to MongoDB
def get_db():
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]


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
    st.markdown("Announcements from provost and president's offices of select universities.")
    db = get_db()

    # Fetch all unique schools from the database
    schools_cursor = db.schools.find({}, {"_id": 0, "code": 1, "name": 1})
    schools = [{"code": school.get("code"), "name": school.get("name", school.get("code"))} for school in schools_cursor]

    # Create a dropdown for school selection
    school_options = ["All"] + [school["name"] for school in schools]
    selected_school = st.selectbox("Filter by School", school_options)

    # Map selected school name back to its code
    selected_school_code = None
    if selected_school != "All":
        selected_school_code = next((school["code"] for school in schools if school["name"] == selected_school), None)

    st.markdown('>_Click the checkbox to see only items pertaining to federal government or federal administration actions. Hover on the question mark to see the prompt used. Please note that this is an unedited **first draft** proof-of-concept. Entries may be missing or incorrect._', unsafe_allow_html=True)

    show_only_llm_related = st.checkbox("👈 LLM Identified as Govt. Related", help="LLM Prompt: Is this an instance of the university either (1) supporting or (2) opposing federal government or federal administration actions?")

    # Build the query based on the selected school
    query = {}
    if selected_school_code:
        query["school"] = selected_school_code

    if show_only_llm_related:
        query["llm_response.related"] = True
    
    # Add date filter for announcements after Jan 1, 2025
    query["date"] = {"$gte": datetime(2025, 1, 1)}

    # Fetch announcements based on the query
    cursor = db.announcements.find(query, {"_id": 0}).sort("date", -1)
    announcements = list(cursor)  # Convert cursor to a list to get its length
    num_announcements = len(announcements)

    st.write(f"Number of announcements: **{num_announcements}** (from Jan 1, 2025 onwards)")

    for ann in announcements:
        title = ann.get("title", "No Title")

        # Format the date
        date_value = ann.get("date")
        if isinstance(date_value, datetime):
            date_str = date_value.strftime("%Y-%m-%d")
        else:
            date_str = str(date_value)

        # Resolve school display name and color
        school_code = ann.get("school", "")
        school_name = school_code
        school_color = "#000000"  # Default to black if no color is found
        school_doc = db.schools.find_one({"code": school_code}, {"name": 1, "color": 1})
        if school_doc:
            if school_doc.get("name"):
                school_name = school_doc["name"]
            if school_doc.get("color"):
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
            if llm_response.get("related"):
                st.markdown(f"🤖 **LLM Says:** {llm_response['reason']}")
            
        st.markdown("<hr style=\"margin-top:0.5em;margin-bottom:0.5em;\">", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
