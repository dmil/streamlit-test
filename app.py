    
    def process_scrape_data(self, videos, scrape_time):
        """Process and append scrape data"""
        # Create scrape record
        scrape_record = {
            'scrape_id': scrape_time.strftime('%Y%m%d_%H%M%S'),
            'scraped_at': scrape_time.isoformat(),
            'videos_found': len(videos),
            'videos': []
        }
        
        # Process each video
        for video in videos:
            video_id = video.get('id')
            
            # Add exact timestamp
            video['exact_scrape_time'] = scrape_time.isoformat()
            
            # Track in video history
            if video_id:
                if video_id not in self.existing_data['video_history']:
                    self.existing_data['video_history'][video_id] = {
                        'first_seen': scrape_time.isoformat(),
                        'description': video.get('description', ''),
                        'metrics_history': []
                    }
                
                # Add metrics snapshot
                metrics_snapshot = {
                    'timestamp': scrape_time.isoformat(),
                    'views': video.get('views'),
                    'likes': video.get('likes'),
                    'comments': video.get('comments'),
                    'shares': video.get('shares'),
                    'bookmarks': video.get('bookmarks')
                }
                
                self.existing_data['video_history'][video_id]['metrics_history'].append(metrics_snapshot)
            
            scrape_record['videos'].append(video)
        
        # Append to all scrapes
        self.existing_data['all_scrapes'].append(scrape_record)
        
        # Update metadata
        self.existing_data['last_updated'] = scrape_time.isoformat()
        self.existing_data['total_unique_videos'] = len(self.existing_data['video_history'])
        
        # Save to file
        self.save_data()
        
        # Print summary
        self.print_summary(scrape_record)
    
    def save_data(self):
        """Save data to JSON file"""
        os.makedirs("output", exist_ok=True)
        
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.existing_data, f, indent=2, ensure_ascii=False)
        
        print(f"💾 Data saved to {self.filename}")
    
    def print_summary(self, scrape_record):
        """Print summary of the scrape"""
        print(f"\n📈 Metrics Summary:")
        
        # Find videos with highest engagement
        videos_with_views = [v for v in scrape_record['videos'] if v.get('views')]
        if videos_with_views:
            sorted_videos = sorted(
                videos_with_views,
                key=lambda x: self.parse_metric(x.get('views', '0')),
                reverse=True
            )[:3]
            
            print("   Top videos by views:")
            for video in sorted_videos:
                desc = video.get('description', '')[:40]
                print(f"   - {desc}...")
                print(f"     Views: {video.get('views')}, Likes: {video.get('likes')}")
    
    def parse_metric(self, metric_str):
        """Convert metric strings to numbers"""
        if not metric_str:
            return 0
        
        metric_str = str(metric_str).strip()
        multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000}
        
        for suffix, multiplier in multipliers.items():
            if suffix in metric_str.upper():
                try:
                    number = float(re.sub(r'[^0-9.]', '', metric_str.replace(suffix, '')))
                    return int(number * multiplier)
                except:
                    return 0
        
        try:
            return int(re.sub(r'[^0-9]', '', metric_str))
        except:
            return 0


def run_scraper():
    """Function to run the scraper - used by scheduler"""
    print(f"\n🔄 Scheduled scrape starting...")
    scraper = TikTokMetadataScraper(username="whitehouse", append_mode=True)
    asyncio.run(scraper.run())
    print(f"✅ Scheduled scrape completed\n")


def setup_scheduler():
    """Setup automatic scheduling"""
    print("⏰ Setting up automatic scheduling...")
    print("   Scheduled times: 08:00, 12:00, 16:00, 20:00")
    
    # Schedule 4 times a day
    schedule.every().day.at("08:00").do(run_scraper)
    schedule.every().day.at("12:00").do(run_scraper)
    schedule.every().day.at("16:00").do(run_scraper)
    schedule.every().day.at("20:00").do(run_scraper)
    
    print("📅 Scheduler is running. Press Ctrl+C to stop.")
    print(f"   Next run: {schedule.next_run()}")
    
    # Keep running
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


async def main():
    """Main function with options"""
    if len(sys.argv) > 1:
        if sys.argv[1] == "--schedule":
            # Run with scheduler
            setup_scheduler()
        elif sys.argv[1] == "--once":
            # Run once
            scraper = TikTokMetadataScraper(username="whitehouse", append_mode=True)
            await scraper.run()
    else:
        # Default: run once
        scraper = TikTokMetadataScraper(username="whitehouse", append_mode=True)
        await scraper.run()


if __name__ == "__main__":
    print("🚀 TikTok Metadata Scraper - Automated Version")
    print("=" * 60)
    print("Usage:")
    print("  python scraper.py           # Run once")
    print("  python scraper.py --once    # Run once")
    print("  python scraper.py --schedule # Run 4x daily automatically")
    print("=" * 60)
    
    # Install schedule library if needed
    try:
        import schedule
    except ImportError:
        print("\n⚠️  Installing required 'schedule' library...")
        os.system("pip install schedule")
        import schedule
    
    asyncio.run(main())