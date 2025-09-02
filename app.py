#!/usr/bin/env python3
"""
Simple Streamlit front-end to display announcements from MongoDB.
Shows title, school, date, URL, and source base URL for each announcement.
"""

import os
import requests
from datetime import datetime, timezone, timedelta
import streamlit as st
from pymongo import MongoClient
import pandas as pd
import io
import pytz
from tzlocal import get_localzone
import json

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(override=True)

# Configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "campus_data")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")

# Define the start date for filtering announcements
start_date = datetime(2025, 1, 1)

def utc_to_local(utc_dt):
    """Function to convert UTC datetime to local time with robust timezone handling"""
    if utc_dt is None:
        return None
    if not isinstance(utc_dt, datetime):
        return utc_dt
    
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    try:
        local_tz = get_localzone()
        local_dt = utc_dt.astimezone(local_tz)
        return local_dt
    except Exception as e:
        print(f"Error converting timezone: {e}")
        return utc_dt if utc_dt.tzinfo else utc_dt.replace(tzinfo=timezone.utc)

def ensure_timezone_aware(dt):
    """Utility function to ensure datetime is timezone-aware (assumes UTC if naive)"""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

@st.cache_resource
def get_db():
    """Connect to MongoDB"""
    client = MongoClient(
        MONGO_URI, 
        maxPoolSize=50, 
        minPoolSize=5, 
        maxIdleTimeMS=30000,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000
    )
    return client[DB_NAME]

def get_filtered_count(query):
    """Get count of documents matching query"""
    try:
        db = get_db()
        return db.articles.count_documents(query, maxTimeMS=10000)
    except Exception as e:
        print(f"Error counting documents: {e}")
        return 0

@st.cache_data(ttl=300)
def get_organizations_data(mongo_uri, db_name):
    """Get all organizations data"""
    client = MongoClient(mongo_uri)
    db = client[db_name]
    orgs_cursor = db.orgs.find({}, {"name": 1, "color": 1, "scrapers": 1})
    return list(orgs_cursor)

@st.cache_data(ttl=300)
def get_scraper_mapping(_organizations_data):
    """Create mapping from scraper path to scraper info"""
    scraper_mapping = {}
    scraper_types = set()
    
    for org in _organizations_data:
        org_name = org.get("name", "Unknown School")
        org_color = org.get("color", "#000000")
        scrapers = org.get("scrapers", [])
        
        for scraper in scrapers:
            path = scraper.get("path", "")
            name = scraper.get("name", "")
            
            if path:
                scraper_mapping[path] = {
                    "name": name,
                    "org_name": org_name,
                    "org_color": org_color
                }
            
            if name:
                scraper_types.add(name)
    
    return scraper_mapping, sorted(list(scraper_types))

def get_scraper_paths_by_type(_organizations_data, scraper_type):
    """Get all scraper paths that match a specific type name"""
    matching_paths = []
    for org in _organizations_data:
        scrapers = org.get("scrapers", [])
        for scraper in scrapers:
            if scraper.get("name") == scraper_type:
                path = scraper.get("path")
                if path:
                    matching_paths.append(path)
    return matching_paths

def send_slack_notification(failed_scrapers, daily_stats=None):
    """Send comprehensive Slack notification for scraper health and daily overview"""
    if not SLACK_WEBHOOK_URL:
        return False
    
    # Build comprehensive daily report message
    message = "📊 **Daily Campus Scraper Report**\n\n"
    
    # Daily stats overview
    if daily_stats:
        message += f"**📈 Today's Activity:**\n"
        message += f"• New announcements: {daily_stats['new_announcements']}\n"
        message += f"• Schools with updates: {daily_stats['active_schools']}\n"
        message += f"• Total announcements in system: {daily_stats['total_announcements']:,}\n\n"
        
        if daily_stats['top_schools']:
            message += f"**🏆 Most Active Schools Today:**\n"
            for school, count in daily_stats['top_schools'][:3]:
                message += f"• {school}: {count} new announcements\n"
            message += "\n"
    
    # Health status
    if failed_scrapers:
        message += f"**🚨 BROKEN SCRAPERS ({len(failed_scrapers)} need fixing):**\n"
        for scraper in failed_scrapers:
            message += f"• {scraper['School']} - {scraper['Name']}\n"
            message += f"  ❌ {scraper['Health Reason']}\n"
        message += "\n"
    else:
        message += "**✅ All scrapers healthy!**\n\n"
    
    message += f"View dashboard: https://campusdata.onrender.com/"
    
    payload = {
        "text": message,
        "username": "Campus Scraper Monitor",
        "icon_emoji": ":clipboard:"
    }
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Slack notification: {e}")
        return False

def get_daily_stats(db):
    """Get daily statistics for Slack notification"""
    try:
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day)
        
        # Get new announcements today
        new_announcements = db.articles.count_documents({
            "date": {"$gte": today_start}
        })
        
        # Get total announcements
        total_announcements = db.articles.count_documents({})
        
        # Get schools with updates today
        pipeline = [
            {"$match": {"date": {"$gte": today_start}}},
            {"$group": {"_id": "$org", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]
        
        school_updates = list(db.articles.aggregate(pipeline))
        active_schools = len(school_updates)
        top_schools = [(item["_id"], item["count"]) for item in school_updates]
        
        return {
            "new_announcements": new_announcements,
            "active_schools": active_schools,
            "total_announcements": total_announcements,
            "top_schools": top_schools
        }
    except Exception as e:
        print(f"Error getting daily stats: {e}")
        return None

def check_scraper_health():
    """Check scraper health and send daily notification if needed"""
    try:
        db = get_db()
        organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
        
        # Check if we've already sent a report today
        today = datetime.now().date()
        
        try:
            last_report = db.slack_reports.find_one({"type": "daily_report"})
            if last_report and last_report.get("date"):
                last_report_date = last_report["date"]
                if isinstance(last_report_date, datetime):
                    if last_report_date.tzinfo is None:
                        last_report_date = last_report_date.replace(tzinfo=timezone.utc)
                    last_report_date = last_report_date.date()
                elif isinstance(last_report_date, str):
                    last_report_date = datetime.fromisoformat(last_report_date).date()
                
                # If we already sent a report today, skip
                if last_report_date >= today:
                    return False, "Already sent today"
        except Exception as e:
            print(f"Error checking last report date: {e}")
        
        # Get failed scrapers
        failed_scrapers = []
        current_time = datetime.now(timezone.utc)
        
        for org in organizations_data:
            school_name = org.get("name", "Unknown School")
            scrapers = org.get("scrapers", [])
            
            for scraper in scrapers:
                last_run = scraper.get("last_run")
                if last_run and isinstance(last_run, datetime):
                    if last_run.tzinfo is None:
                        last_run = last_run.replace(tzinfo=timezone.utc)
                    
                    hours_since_run = (current_time - last_run).total_seconds() / 3600
                    
                    # Consider broken if not run in 25+ hours (allows for some delay)
                    if hours_since_run > 25:
                        failed_scrapers.append({
                            "School": school_name,
                            "Name": scraper.get("name", "").replace(" announcements", ""),
                            "Health Reason": f"Last run {int(hours_since_run)}h ago"
                        })
                else:
                    failed_scrapers.append({
                        "School": school_name,
                        "Name": scraper.get("name", "").replace(" announcements", ""),
                        "Health Reason": "No run data available"
                    })
        
        # Get daily stats
        daily_stats = get_daily_stats(db)
        
        # Send notification
        success = send_slack_notification(failed_scrapers, daily_stats)
        
        if success:
            # Update the tracking record
            db.slack_reports.update_one(
                {"type": "daily_report"},
                {"$set": {"date": today, "sent_at": datetime.now(timezone.utc)}},
                upsert=True
            )
            return True, f"Daily report sent: {len(failed_scrapers)} broken scrapers"
        else:
            return False, "Failed to send daily report"
            
    except Exception as e:
        print(f"Error in health check: {e}")
        return False, str(e)

def get_paginated_announcements(query_dict, page, page_size):
    """Get paginated announcements"""
    try:
        db = get_db()
        start_idx = page * page_size
        
        projection = {
            "_id": 0,
            "title": 1,
            "org": 1, 
            "date": 1,
            "scraper": 1,
            "url": 1,
            "content": 1,
            "llm_response": 1
        }
        
        cursor = db.articles.find(query_dict, projection).sort("date", -1).skip(start_idx).limit(page_size).max_time_ms(10000)
        announcements = list(cursor)
        
        for ann in announcements:
            if 'date' in ann and ann['date']:
                ann['date'] = ensure_timezone_aware(ann['date'])
        
        return announcements
    except Exception as e:
        print(f"Error fetching announcements: {e}")
        return []

def convert_to_csv(announcements, scraper_mapping):
    """Convert announcements data to CSV format"""
    processed_data = []
    
    for ann in announcements:
        processed_ann = {
            "title": ann.get("title", ""),
            "school": ann.get("org", ""),
            "date": ann.get("date"),
            "url": ann.get("url", ""),
        }
        
        scraper_path = ann.get("scraper", "")
        scraper_type = "Unknown Type"
        if scraper_path in scraper_mapping:
            scraper_type = scraper_mapping[scraper_path]["name"]
        
        processed_ann["announcement_type"] = scraper_type
        
        llm_response = ann.get("llm_response", {})
        
        classification_fields = [
            "government_related", "lawsuit_related", "funding_related", 
            "protest_related", "layoff_related", "trump_related"
        ]
        
        for field_name in classification_fields:
            field_data = llm_response.get(field_name, {})
            processed_ann[f"{field_name}"] = field_data.get("related", False)
            processed_ann[f"{field_name}_reason"] = field_data.get("reason", "") if field_data.get("related") else ""
        
        processed_data.append(processed_ann)
    
    df = pd.DataFrame(processed_data)
    
    if 'date' in df.columns:
        df['date'] = df['date'].apply(lambda x: utc_to_local(x).strftime('%Y-%m-%d %I:%M:%S %p') if isinstance(x, datetime) else str(x))
    
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    return csv_buffer.getvalue()

def display_dashboard_tab(db):
    """Comprehensive dashboard with stats and insights"""
    st.markdown("### 📊 Campus Announcements Dashboard")
    
    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    
    # === KEY METRICS ROW ===
    col1, col2, col3, col4 = st.columns(4)
    
    # Basic counts
    total_orgs = len(organizations_data)
    total_announcements = db.articles.count_documents({"date": {"$gte": start_date}})
    
    # Health metrics
    current_time = datetime.now(timezone.utc)
    broken_scrapers = 0
    total_scrapers = 0
    for org in organizations_data:
        for scraper in org.get("scrapers", []):
            total_scrapers += 1
            last_run = scraper.get("last_run")
            if last_run and isinstance(last_run, datetime):
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                hours_since_run = (current_time - last_run).total_seconds() / 3600
                if hours_since_run > 25:
                    broken_scrapers += 1
            else:
                broken_scrapers += 1
    
    # Today's activity
    today = datetime.now()
    today_start = datetime(today.year, today.month, today.day)
    schools_updated_today = len(db.articles.distinct("org", {"date": {"$gte": today_start}}))
    announcements_today = db.articles.count_documents({"date": {"$gte": today_start}})
    
    with col1:
        st.metric("Total Schools", total_orgs)
    with col2:
        st.metric("Total Announcements", f"{total_announcements:,}")
    with col3:
        st.metric("Schools Active Today", f"{schools_updated_today}/{total_orgs}")
    with col4:
        health_color = "🟢" if broken_scrapers == 0 else "🔴"
        healthy_scrapers = total_scrapers - broken_scrapers
        st.metric(f"{health_color} System Health", f"{healthy_scrapers}/{total_scrapers} OK")
    
    # === RECENT ACTIVITY ===
    st.markdown("### 📈 Recent Activity")
    
    activity_col1, activity_col2 = st.columns(2)
    
    with activity_col1:
        st.markdown("**📅 Last 7 Days**")
        week_ago = datetime.now() - timedelta(days=7)
        daily_counts = []
        
        for i in range(7):
            day = week_ago + timedelta(days=i)
            day_start = datetime(day.year, day.month, day.day)
            day_end = day_start + timedelta(days=1)
            
            count = db.articles.count_documents({
                "date": {"$gte": day_start, "$lt": day_end}
            })
            daily_counts.append({
                "Date": day.strftime("%m/%d"),
                "Announcements": count
            })
        
        if daily_counts:
            daily_df = pd.DataFrame(daily_counts)
            st.dataframe(daily_df, hide_index=True, use_container_width=True)
    
    with activity_col2:
        st.markdown("**🏆 Most Active Schools (30 days)**")
        month_ago = datetime.now() - timedelta(days=30)
        
        pipeline = [
            {"$match": {"date": {"$gte": month_ago}}},
            {"$group": {"_id": "$org", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]
        
        top_schools = list(db.articles.aggregate(pipeline))
        
        if top_schools:
            school_data = [{"School": item["_id"], "Count": item["count"]} for item in top_schools]
            schools_df = pd.DataFrame(school_data)
            st.dataframe(schools_df, hide_index=True, use_container_width=True)
        else:
            st.info("No recent activity")
    
    # === CONTENT INSIGHTS ===
    st.markdown("### 🔍 Content Categories")
    
    categories = [
        ("government_related", "Government Related", "🏛️"),
        ("lawsuit_related", "Lawsuit Related", "⚖️"), 
        ("funding_related", "Funding Related", "💰"),
        ("protest_related", "Protest Related", "📢"),
        ("layoff_related", "Layoff Related", "📉"),
        ("trump_related", "Trump Related", "🇺🇸")
    ]
    
    # Create two columns for categories
    cat_col1, cat_col2 = st.columns(2)
    
    category_data = []
    for field, display_name, emoji in categories:
        count = db.articles.count_documents({
            f"llm_response.{field}.related": True,
            "date": {"$gte": start_date}
        })
        category_data.append({
            "Category": f"{emoji} {display_name}",
            "Count": count,
            "% of Total": f"{(count/total_announcements*100):.1f}%" if total_announcements > 0 else "0%"
        })
    
    with cat_col1:
        if category_data:
            # First 3 categories
            cat_df1 = pd.DataFrame(category_data[:3])
            st.dataframe(cat_df1, hide_index=True, use_container_width=True)
    
    with cat_col2:
        if category_data:
            # Last 3 categories
            cat_df2 = pd.DataFrame(category_data[3:])
            st.dataframe(cat_df2, hide_index=True, use_container_width=True)
    


def display_system_health_tab(db):
    """Combined scraper and school health monitoring"""
    st.markdown("### 🔧 System Health")
    
    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    
    if not organizations_data:
        st.warning("No schools found in the database.")
        return
    
    # === HEALTH OVERVIEW ===
    current_time = datetime.now(timezone.utc)
    
    health_stats = {
        "healthy_scrapers": 0,
        "broken_scrapers": 0,
        "recent_schools": 0,
        "quiet_schools": 0,
        "stale_schools": 0,
        "no_posts_schools": 0
    }
    
    detailed_data = []
    
    for org in organizations_data:
        school_name = org.get("name", "Unknown School")
        scrapers = org.get("scrapers", [])
        
        # Check scraper health
        scraper_health_status = "✅ Healthy"
        broken_scraper_details = []
        
        for scraper in scrapers:
            last_run = scraper.get("last_run")
            scraper_name = scraper.get("name", "Unknown")
            
            if last_run and isinstance(last_run, datetime):
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                hours_since_run = (current_time - last_run).total_seconds() / 3600
                
                if hours_since_run > 25:
                    broken_scraper_details.append(f"{scraper_name} ({int(hours_since_run)}h)")
                    health_stats["broken_scrapers"] += 1
                else:
                    health_stats["healthy_scrapers"] += 1
            else:
                broken_scraper_details.append(f"{scraper_name} (no data)")
                health_stats["broken_scrapers"] += 1
        
        if broken_scraper_details:
            scraper_health_status = f"❌ Broken: {', '.join(broken_scraper_details)}"
        
        # Check content freshness
        try:
            latest_announcement = db.articles.find_one(
                {"org": school_name},
                sort=[("date", -1)]
            )
        except:
            latest_announcement = None
        
        if latest_announcement:
            latest_date_obj = latest_announcement.get("date")
            if isinstance(latest_date_obj, datetime):
                if latest_date_obj.tzinfo is None:
                    latest_date_obj = latest_date_obj.replace(tzinfo=timezone.utc)
                
                days_since_post = (current_time - latest_date_obj).total_seconds() / 86400
                
                if days_since_post <= 3:
                    content_status = "🟢 Recent"
                    health_stats["recent_schools"] += 1
                elif days_since_post <= 7:
                    content_status = "🟡 Quiet"
                    health_stats["quiet_schools"] += 1
                else:
                    content_status = "🔴 Stale"
                    health_stats["stale_schools"] += 1
                
                local_date = utc_to_local(latest_date_obj)
                latest_date = local_date.strftime("%Y-%m-%d %I:%M %p")
                latest_title = latest_announcement.get("title", "No Title")[:50] + "..."
            else:
                content_status = "❓ Unknown"
                latest_date = "No Date"
                latest_title = ""
        else:
            content_status = "⚫ No Posts"
            health_stats["no_posts_schools"] += 1
            latest_date = "No Recent Announcements"
            latest_title = ""
        
        # Count total announcements
        try:
            announcement_count = db.articles.count_documents({
                "org": school_name,
                "date": {"$gte": start_date}
            })
        except:
            announcement_count = 0
        
        detailed_data.append({
            "School": school_name,
            "Scraper Health": scraper_health_status,
            "Content Status": content_status,
            "Total Posts": announcement_count,
            "Latest Date": latest_date,
            "Latest Title": latest_title
        })
    
    # === SUMMARY METRICS ===
    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    
    with summary_col1:
        total_scrapers = health_stats["healthy_scrapers"] + health_stats["broken_scrapers"]
        st.metric(
            "Scrapers Health", 
            f"{health_stats['healthy_scrapers']}/{total_scrapers}",
            delta=f"{health_stats['broken_scrapers']} broken" if health_stats['broken_scrapers'] > 0 else "All healthy"
        )
    
    with summary_col2:
        st.metric(
            "Recent Activity",
            f"{health_stats['recent_schools']} schools",
            help="Schools with posts in last 3 days"
        )
    
    with summary_col3:
        st.metric(
            "Quiet Schools",
            f"{health_stats['quiet_schools']}",
            help="Schools with posts 4-7 days ago"
        )
    
    with summary_col4:
        problem_schools = health_stats['stale_schools'] + health_stats['no_posts_schools']
        st.metric(
            "Problem Schools",
            f"{problem_schools}",
            help="Schools with stale content or no posts"
        )
    
    # === DETAILED TABLE ===
    st.markdown("### 📋 Detailed Status")
    
    # Sort: Broken scrapers first, then by latest date
    detailed_data.sort(key=lambda x: (0 if "Broken" in x["Scraper Health"] else 1, x["Latest Date"]), reverse=True)
    
    # Create display DataFrame
    display_df = pd.DataFrame(detailed_data)
    
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "Latest Title": st.column_config.TextColumn(
                "Latest Title",
                width="large"
            ),
            "Scraper Health": st.column_config.TextColumn(
                "Scraper Health",
                width="large"
            )
        }
    )
    


def display_announcements(db):
    """Display the announcements view"""
    st.markdown('Please note that this is an unedited **first draft** proof-of-concept. Classifications **WILL BE** inaccurate.')

    organizations_data = get_organizations_data(MONGO_URI, DB_NAME)
    scraper_mapping, scraper_types = get_scraper_mapping(organizations_data)
    
    school_names = sorted([org["name"] for org in organizations_data])

    st.markdown('_Check any box to filter for items identified by our LLM as related to that category._')

    col1, col2, col3 = st.columns(3)

    with col1:
        show_govt_related = st.checkbox("Government Related", 
            key="show_govt_related_ann",
            help="Items where the university is responding to federal government or administration actions")
        show_lawsuit_related = st.checkbox("Lawsuit Related", 
            key="show_lawsuit_related_ann",
            help="Items mentioning lawsuits or legal actions related to the university")

    with col2:
        show_funding_related = st.checkbox("Funding Related", 
            key="show_funding_related_ann",
            help="Items discussing funding cuts or financial issues")
        show_protest_related = st.checkbox("Protest Related", 
            key="show_protest_related_ann",
            help="Items mentioning campus protests or disruptions")

    with col3:
        show_layoff_related = st.checkbox("Layoff Related", 
            key="show_layoff_related_ann",
            help="Items discussing layoffs, job cuts, staff reductions, or employment terminations")
        show_trump_related = st.checkbox("Trump Related", 
            key="show_trump_related_ann",
            help="Items related to Donald Trump")
    
    search_term = st.text_input("Search announcement content", value="", key="search_term")

    filter_col1, filter_col2 = st.columns(2)
    
    with filter_col1:
        school_options = ["All"] + school_names
        selected_school = st.selectbox("Filter by School", school_options)
    
    with filter_col2:
        scraper_type_options = ["All"] + scraper_types
        selected_scraper_type = st.selectbox("Filter by Announcement Type", scraper_type_options)

    query = {}
    if selected_school != "All":
        query["org"] = selected_school

    if selected_scraper_type != "All":
        matching_paths = get_scraper_paths_by_type(organizations_data, selected_scraper_type)
        if matching_paths:
            query["scraper"] = {"$in": matching_paths}

    filter_conditions = []
    if show_govt_related:
        filter_conditions.append({"llm_response.government_related.related": True})
    if show_lawsuit_related:
        filter_conditions.append({"llm_response.lawsuit_related.related": True})
    if show_funding_related:
        filter_conditions.append({"llm_response.funding_related.related": True})
    if show_protest_related:
        filter_conditions.append({"llm_response.protest_related.related": True})
    if show_layoff_related:
        filter_conditions.append({"llm_response.layoff_related.related": True})
    if show_trump_related:
        filter_conditions.append({"llm_response.trump_related.related": True})

    if filter_conditions:
        query["$or"] = filter_conditions

    query["date"] = {
        "$gte": start_date,
        "$exists": True,
        "$ne": None
    }

    if search_term.strip():
        query["content"] = {"$regex": search_term, "$options": "i"}

    with st.spinner("Counting results..."):
        num_announcements = get_filtered_count(query)

    st.write(f"Number of announcements: **{num_announcements:,}** (from {start_date.strftime('%B %d, %Y')} onwards)")
    
    PAGE_SIZE = 20
    total_pages = max((num_announcements - 1) // PAGE_SIZE + 1, 1) if num_announcements > 0 else 1
    
    filter_state_key = f"{selected_school}_{selected_scraper_type}_{show_govt_related}_{show_lawsuit_related}_{show_funding_related}_{show_protest_related}_{show_layoff_related}_{show_trump_related}_{search_term}"
    
    if "last_filter_state" not in st.session_state:
        st.session_state["last_filter_state"] = filter_state_key
        st.session_state["ann_page"] = 0
    elif st.session_state["last_filter_state"] != filter_state_key:
        st.session_state["ann_page"] = 0
        st.session_state["last_filter_state"] = filter_state_key
    
    if "ann_page" not in st.session_state:
        st.session_state["ann_page"] = 0
    
    st.session_state["ann_page"] = max(0, min(st.session_state["ann_page"], total_pages - 1))
    
    col_download, col_clear = st.columns([1, 3])
    
    with col_download:
        if num_announcements > 0:
            if st.button("Generate CSV"):
                with st.spinner("Generating CSV file..."):
                    all_cursor = db.articles.find(query, {"_id": 0}).sort("date", -1)
                    all_announcements = list(all_cursor)
                    csv = convert_to_csv(all_announcements, scraper_mapping)
                    st.download_button(
                        label="Download CSV",
                        data=csv,
                        file_name="announcements_data.csv",
                        mime="text/csv",
                    )

    with col_clear:
        if st.button("Clear All Filters"):
            for key in list(st.session_state.keys()):
                if key.startswith(('show_', 'search_term', 'ann_page', 'last_filter_state')):
                    del st.session_state[key]
            st.rerun()

    if num_announcements == 0:
        st.info("No announcements found matching your filters.")
        return

    with st.spinner("Loading announcements..."):
        paged_announcements = get_paginated_announcements(query, st.session_state["ann_page"], PAGE_SIZE)

    for ann in paged_announcements:
        title = ann.get("title", "No Title")

        date_value = ann.get("date")
        if isinstance(date_value, datetime):
            date_value = ensure_timezone_aware(date_value)
            local_date = utc_to_local(date_value)
            date_str = local_date.strftime("%Y-%m-%d %I:%M:%S %p")
        else:
            date_str = str(date_value) if date_value else "No Date"

        scraper_path = ann.get("scraper", "")
        school_name = ann.get("org", "Unknown School")
        school_color = "#000000"
        scraper_type_display = "Unknown Type"
        
        if scraper_path in scraper_mapping:
            scraper_info = scraper_mapping[scraper_path]
            scraper_type_display = scraper_info["name"]
            school_color = scraper_info["org_color"]

        url = ann.get("url", "")

        st.subheader(title)
        content = ann.get("content", "")
        announcement_html = f"""
        <p style="margin-bottom: 0.5em;">
            <strong>School:</strong> <span style="background-color:{school_color}; padding:2px 4px; border-radius:4px; color:#ffffff;">{school_name}</span><br>
            <strong>Type:</strong> {scraper_type_display}<br>
            <strong>Date:</strong> {date_str}<br>
            <strong>Content Scraped:</strong> {'✅' if content else '👎'}<br>
            <strong>Announcement URL:</strong><br/> <a href="{url}">{url}</a>
        </p>
        """
        st.markdown(announcement_html, unsafe_allow_html=True)
        
        if search_term.strip() and content:
            import re
            search_pattern = re.compile(re.escape(search_term), re.IGNORECASE)
            matches = list(search_pattern.finditer(content))
            
            if matches:
                snippets = []
                for i, match in enumerate(matches):
                    start_pos = max(0, match.start() - 100)
                    end_pos = min(len(content), match.end() + 100)
                    snippet = content[start_pos:end_pos]
                    
                    if start_pos > 0:
                        snippet = "..." + snippet
                    if end_pos < len(content):
                        snippet = snippet + "..."
                    
                    highlighted_snippet = search_pattern.sub(f"<mark style='background-color: #ffeb3b; color: #000000; padding: 2px;'>{search_term}</mark>", snippet)
                    snippets.append(highlighted_snippet)
                
                match_count = len(matches)
                match_text = "match" if match_count == 1 else "matches"
                
                snippets_html = "<br/><br/>".join([f"<strong>Match {i+1}:</strong><br/><em>{snippet}</em>" for i, snippet in enumerate(snippets)])
                
                st.markdown(f"""
                <div style="background-color: rgba(255, 255, 255, 0.1); padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #ff6b6b;">
                    <strong>Search Results ({match_count} {match_text}):</strong><br/>
                    <div>{snippets_html}</div>
                </div>
                """, unsafe_allow_html=True)

        if ann.get("llm_response"):
            llm_response = ann.get("llm_response")
            categories_found = []

            if show_govt_related and llm_response.get("government_related", {}).get("related"):
                categories_found.append(("Government", llm_response["government_related"].get("reason", "")))

            if show_lawsuit_related and llm_response.get("lawsuit_related", {}).get("related"):
                categories_found.append(("Lawsuit", llm_response["lawsuit_related"].get("reason", "")))

            if show_funding_related and llm_response.get("funding_related", {}).get("related"):
                categories_found.append(("Funding", llm_response["funding_related"].get("reason", "")))

            if show_protest_related and llm_response.get("protest_related", {}).get("related"):
                categories_found.append(("Protest", llm_response["protest_related"].get("reason", "")))

            if show_layoff_related and llm_response.get("layoff_related", {}).get("related"):
                categories_found.append(("Layoffs", llm_response["layoff_related"].get("reason", "")))

            if show_trump_related and llm_response.get("trump_related", {}).get("related"):
                categories_found.append(("Trump", llm_response["trump_related"].get("reason", "")))

            for category, reason in categories_found:
                st.markdown(f"**AI Classification ({category}):** {reason}")

        st.markdown("<hr style=\"margin-top:0.5em;margin-bottom:0.5em;\">", unsafe_allow_html=True)

    if total_pages > 1:
        st.markdown("<br>", unsafe_allow_html=True)
        
        col_prev, col_page, col_next = st.columns([1,2,1])
        
        with col_prev:
            prev_disabled = st.session_state["ann_page"] == 0
            if st.button("Prev", key="ann_prev_unique", disabled=prev_disabled):
                if st.session_state["ann_page"] > 0:
                    st.session_state["ann_page"] -= 1
                    st.rerun()
        
        with col_page:
            st.markdown(f"<div style='text-align:center; padding-top:8px;'>Page <b>{st.session_state['ann_page']+1}</b> of <b>{total_pages}</b></div>", unsafe_allow_html=True)
        
        with col_next:
            next_disabled = st.session_state["ann_page"] >= total_pages - 1
            if st.button("Next", key="ann_next_unique", disabled=next_disabled):
                if st.session_state["ann_page"] < total_pages - 1:
                    st.session_state["ann_page"] += 1
                    st.rerun()

def main():
    st.set_page_config(
        page_title="Campus Announcements Tracker [DRAFT]",
        page_icon="🎓",
        layout="wide",  # Changed to wide for better dashboard layout
        initial_sidebar_state="expanded",
        menu_items={"About": "This is a draft version of the Campus Announcements Tracker."}
    )
    
    st.title("Campus Announcements [DRAFT]")
    st.markdown("Announcements from the provosts' and presidents' offices at select universities.")
    
    try:
        db = get_db()
        
        # Test database connection
        try:
            test_count = db.articles.count_documents({})
            print(f"Database connection successful. Total articles: {test_count}")
        except Exception as db_test_error:
            st.error(f"Database query error: {db_test_error}")
            return
        
        # Create three streamlined tabs
        tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "📋 Announcements", "🔧 System Health"])
        
        with tab1:
            try:
                display_dashboard_tab(db)
            except Exception as e:
                st.error(f"Error in dashboard: {e}")
        
        with tab2:
            try:
                display_announcements(db)
            except Exception as e:
                st.error(f"Error in announcements: {e}")
        
        with tab3:
            try:
                display_system_health_tab(db)
            except Exception as e:
                st.error(f"Error in system health: {e}")
            
    except Exception as e:
        st.error(f"Database connection error: {e}")

if __name__ == "__main__":
    main()