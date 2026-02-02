# SEO & Analytics Setup Guide for IL9Cast

This guide will help you set up Google Analytics tracking and optimize your site for search engines.

## 🎯 Quick Start Checklist

- [ ] Set up Google Analytics
- [ ] Replace placeholder Analytics ID in templates
- [ ] Submit site to Google Search Console
- [ ] Submit sitemap to Google
- [ ] Monitor analytics and search performance

---

## 1. Google Analytics Setup

### Create a Google Analytics Account

1. Go to [Google Analytics](https://analytics.google.com/)
2. Sign in with your Google account
3. Click **"Start measuring"**
4. Enter account details:
   - Account name: `IL9Cast`
   - Data sharing settings: (choose based on preference)
5. Click **"Next"**

### Create a Property

1. Property name: `IL9Cast`
2. Reporting time zone: `United States - Central Time`
3. Currency: `United States Dollar (USD)`
4. Click **"Next"**

### Set Up Data Stream

1. Choose platform: **Web**
2. Website URL: `https://il9.org`
3. Stream name: `IL9Cast Website`
4. Click **"Create stream"**

### Get Your Measurement ID

1. After creating the stream, you'll see your **Measurement ID** (format: `G-XXXXXXXXXX`)
2. **Copy this ID** - you'll need it in the next step

---

## 2. Add Your Analytics ID to the Site

Replace the placeholder `G-XXXXXXXXXX` with your actual Measurement ID in these files:

```bash
templates/landing_new.html
templates/markets.html
templates/methodology.html
templates/about.html
templates/odds.html
templates/fundraising.html
templates/candidates.html
```

### Find and Replace

In each file, find this line:
```html
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
```

And this line:
```javascript
gtag('config', 'G-XXXXXXXXXX');
```

Replace **both instances** of `G-XXXXXXXXXX` with your actual Measurement ID.

### Quick Replace Command

```bash
# Replace in all templates at once (replace YOUR_ID with your actual ID)
find templates/ -name "*.html" -type f -exec sed -i 's/G-XXXXXXXXXX/YOUR_ID/g' {} +
```

---

## 3. Google Search Console Setup

### Add Your Property

1. Go to [Google Search Console](https://search.google.com/search-console)
2. Click **"Add property"**
3. Choose **"URL prefix"**
4. Enter: `https://il9.org`
5. Click **"Continue"**

### Verify Ownership

**Recommended Method: HTML Tag**
1. Search Console will provide an HTML meta tag
2. Add it to `templates/landing_new.html` in the `<head>` section
3. Deploy the changes
4. Click **"Verify"** in Search Console

**Alternative: Google Analytics**
- If you're already signed in with the same Google account used for Analytics, verification may be automatic

---

## 4. Submit Your Sitemap

### In Google Search Console:

1. In the left sidebar, click **"Sitemaps"**
2. Enter: `sitemap.xml`
3. Click **"Submit"**

Your sitemap is already live at: `https://il9.org/sitemap.xml`

Google will start crawling your pages within 24-48 hours.

---

## 5. Additional SEO Improvements

### Get Backlinks

- Share on social media (Twitter, Reddit, Facebook)
- Post on election-focused forums and communities
- Reach out to political bloggers and journalists
- Submit to prediction market aggregator sites

### Content Updates for SEO

Consider adding these pages/sections:
- Blog posts about the race (fresh content helps SEO)
- Candidate comparison tools
- Election timeline/key dates
- FAQ section

### Social Media Sharing

When sharing on Twitter/Facebook, your Open Graph tags will automatically show:
- Title: "IL9Cast - Illinois 9th District Democratic Primary 2026 Forecast"
- Description: "Live prediction market aggregation..."
- These tags are already configured in all templates

---

## 6. Monitor Your Analytics

### Google Analytics Dashboard

After 24-48 hours, you'll start seeing data:

1. **Realtime** - See current visitors
2. **Acquisition** - How users find your site
3. **Engagement** - Which pages they visit
4. **Demographics** - Where they're located

### Key Metrics to Track

- **Users** - Total unique visitors
- **Sessions** - Total visits
- **Page views** - Total pages viewed
- **Bounce rate** - % who leave after one page
- **Average session duration** - Time spent on site
- **Top pages** - Most visited pages
- **Traffic sources** - Where visitors come from (Google, social media, direct, etc.)

### Google Search Console Metrics

- **Impressions** - How often your site appears in Google results
- **Clicks** - How many people click through from Google
- **Click-through rate (CTR)** - Percentage who click
- **Average position** - Where you rank for search queries
- **Top queries** - What searches bring people to your site

---

## 7. SEO Features Already Implemented

✅ **Meta Tags**
- Title, description, keywords on all pages
- Open Graph tags for social media
- Twitter Card tags

✅ **Structured Data (JSON-LD)**
- WebSite schema
- Event schema (for the primary election)

✅ **Technical SEO**
- Sitemap.xml at `/sitemap.xml`
- Robots.txt at `/robots.txt`
- Canonical URLs on all pages
- Mobile-responsive design
- Fast load times

✅ **Analytics**
- Google Analytics on all pages
- Event tracking ready to configure

---

## 8. Advanced: Custom Event Tracking

Once Analytics is set up, you can track custom events:

### Example: Track Chart Interactions

Add to your JavaScript:
```javascript
// Track when users change chart period
document.querySelectorAll('.toggle-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        gtag('event', 'chart_period_change', {
            'period': btn.dataset.period
        });
    });
});
```

### Example: Track Downloads

```javascript
// Track snapshot downloads
document.getElementById('downloadButton').addEventListener('click', () => {
    gtag('event', 'download', {
        'file_type': 'JSONL'
    });
});
```

---

## 9. Expected Timeline

- **Day 1-2**: Google Analytics starts collecting data
- **Day 3-7**: Google Search Console verification complete
- **Week 1-2**: Google starts indexing your pages
- **Week 2-4**: You start appearing in search results for "IL9Cast"
- **Month 1-2**: Rankings improve for broader terms like "Illinois 9th district primary"

---

## 10. SEO Keywords to Target

Your site is already optimized for these searches:

**Primary Keywords:**
- IL9Cast
- Illinois 9th District Democratic Primary
- Illinois 9th District Primary 2026
- IL9 primary prediction
- Illinois 9th congressional district election

**Secondary Keywords:**
- Manifold Markets Illinois
- Kalshi Illinois primary
- Daniel Biss election odds
- Jan Schakowsky primary
- Prediction markets Illinois

**Long-tail Keywords:**
- Who will win Illinois 9th District Democratic Primary
- Illinois 9th District primary forecast 2026
- IL9 prediction market aggregator

---

## 11. Troubleshooting

### Analytics Not Showing Data

- Wait 24-48 hours after setup
- Check that the Measurement ID is correct
- Verify the site is deployed with the changes
- Check browser console for errors
- Make sure ad blockers aren't blocking analytics

### Site Not Appearing in Google

- Wait 1-2 weeks after sitemap submission
- Check Search Console for crawl errors
- Ensure robots.txt allows Google (already configured)
- Build backlinks to help Google discover your site

### Low Search Rankings

- Create more content (blog posts, updates)
- Get backlinks from political news sites
- Share on social media regularly
- Update content frequently (Google favors fresh content)

---

## 12. Support & Resources

- [Google Analytics Help](https://support.google.com/analytics)
- [Google Search Console Help](https://support.google.com/webmasters)
- [SEO Starter Guide](https://developers.google.com/search/docs/beginner/seo-starter-guide)

---

## Quick Commands Reference

```bash
# Find and replace Analytics ID (replace YOUR_ID with actual ID)
find templates/ -name "*.html" -type f -exec sed -i 's/G-XXXXXXXXXX/YOUR_ID/g' {} +

# Test sitemap
curl https://il9.org/sitemap.xml

# Test robots.txt
curl https://il9.org/robots.txt

# Deploy changes
git add .
git commit -m "Add Google Analytics tracking ID"
git push origin claude/seo-analytics-setup-YR9jZ
```

---

**Questions?** Open an issue on GitHub or check the documentation links above.
