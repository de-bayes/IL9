# Deployment Guide - IL9Cast Recovery

## ✅ Completed Tasks

1. **Data Recovery** - Deduplicated 175 millisecond-duplicate snapshots
2. **JSONL Migration** - Converted to corruption-proof format (35% space savings)
3. **UI Restoration** - Reverted to classic "Big Blue Bars" layout
4. **Data Gap Disclaimer** - Added to markets page
5. **Railway Path Verification** - All paths consistent

## 📋 Pre-Deployment Checklist

### 1. Test Locally (Recommended)

```bash
# Start the Flask app
python3 app.py

# Visit http://localhost:8000/markets in your browser
# Verify:
# - Big blue bars display correctly
# - No candidate photos appear
# - Data gap disclaimer visible at bottom
# - Chart loads with historical data
# - Data collection runs every 60 seconds (check console logs)
```

### 2. Review Changes

```bash
# See what files changed
git status

# Review the key changes
git diff app.py
git diff templates/markets.html
git diff .gitignore
```

## 🚀 Deployment Steps

### Step 1: Commit Changes

```bash
git add .gitignore
git add app.py
git add templates/markets.html
git add CLAUDE.md
git add RECOVERY_SUMMARY.md
git add DEPLOYMENT_GUIDE.md
git add data_recovery.py
git add deduplicate_aggressive.py
git add convert_to_jsonl.py

git commit -m "Major data recovery and UI restoration

- Migrate to JSONL format to prevent corruption (35% space savings)
- Remove 175 millisecond-duplicate snapshots (3% reduction)
- Revert to classic Big Blue Bars layout (no photos, minimal design)
- Add data gap disclaimer for Jan 28-29 Railway maintenance
- Verify Railway volume paths for data persistence

Technical improvements:
- JSONL append-only writes prevent corruption on container restart
- Atomic file operations eliminate 'Extra data' errors
- 35% reduction in file size (2.42 MB vs 3.74 MB)
- Simplified data collection logic

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

### Step 2: Push to Railway

```bash
git push origin main
```

Railway will automatically:
1. Detect the push
2. Build the new version
3. Deploy with zero downtime
4. Preserve existing data in `/app/data` volume

### Step 3: Monitor Deployment

**Check Railway Logs:**
```
Railway Dashboard → Your Project → Deployments → View Logs
```

**Look for:**
- ✅ "Migrating from JSON to JSONL format..." (if first deploy)
- ✅ "Seeded X snapshots in JSONL format" (if no data exists)
- ✅ "Running automatic data collection..."
- ✅ "Snapshot saved successfully. Total snapshots: X"

**Watch for errors:**
- ❌ "Error loading snapshots"
- ❌ "Error appending to JSONL"
- ❌ File permission errors

### Step 4: Verify Production

**Visit your live site:**
```
https://your-app.railway.app/markets
```

**Verify:**
- [ ] Big blue bars visible (no photos)
- [ ] Candidate names and percentages display
- [ ] Chart shows historical trends
- [ ] Data gap disclaimer at bottom
- [ ] New snapshots appear every 60 seconds

**Check data file:**
```bash
# SSH into Railway container (if needed)
railway shell

# Check JSONL file
ls -lh /app/data/historical_snapshots.jsonl
tail /app/data/historical_snapshots.jsonl

# Each line should be a complete JSON object
```

## 🔄 What Happens on First Deploy

1. **Automatic Migration**: If `historical_snapshots.json` exists but `.jsonl` doesn't:
   - Reads all JSON snapshots
   - Writes each to JSONL (one per line)
   - Creates backup of JSON file
   - Logs: "Migrated X snapshots to JSONL"

2. **Continued Collection**: New snapshots append to `.jsonl` file
   - One line added every 60 seconds
   - No file corruption risk
   - No "Extra data" errors

3. **UI Change**: Markets page shows classic blue bars
   - No photos load (image files not needed)
   - Simplified, minimal design
   - Data gap disclaimer appears

## 📊 Expected Behavior

### Data Collection
- **Frequency**: Every 60 seconds
- **File Growth**: ~430 bytes per snapshot
- **Format**: One JSON object per line in `.jsonl` file
- **Corruption Risk**: Zero (atomic line append)

### File Sizes
- **Before**: 3.74 MB JSON array
- **After**: 2.42 MB JSONL (35% savings)
- **Per Snapshot**: ~430 bytes average

### UI Performance
- **Initial Load**: Fetches full JSONL file (~2.4 MB)
- **Chart Rendering**: Filters by time period (1d/7d/all)
- **Auto-Refresh**: Updates every 3 minutes

## ⚠️ Troubleshooting

### "Migrated 0 snapshots" in logs
**Cause**: No existing JSON file found
**Action**: Normal for fresh deployments. Data will start collecting immediately.

### Chart shows "Collecting historical data..."
**Cause**: Fewer than 2 snapshots in file
**Action**: Wait 2-3 minutes for data collection to run. Refresh page.

### Bars show "Loading..." indefinitely
**Cause**: API fetch error (Manifold or Kalshi down)
**Action**: Check Railway logs for API errors. Services may be temporarily unavailable.

### Data gaps continue after deployment
**Cause**: Container restarts or Railway maintenance
**Action**: JSONL format prevents corruption but doesn't prevent gaps from downtime. This is expected and disclosed in disclaimer.

## 🎯 Success Criteria

✅ Deployment successful if:
1. Markets page loads without errors
2. Blue bars display with candidate data
3. Chart renders with historical trends
4. New snapshots append every 60 seconds
5. Data gap disclaimer visible
6. No "Extra data" errors in logs
7. File size grows linearly (~430 bytes/min)

## 🔙 Rollback Plan (If Needed)

If something goes wrong:

```bash
# Revert to previous commit
git revert HEAD

# Or restore from backup
git checkout HEAD~1 -- app.py templates/markets.html

git commit -m "Rollback to previous version"
git push origin main
```

Railway will auto-deploy the previous version. Your data is safe in the persistent volume.

## 📝 Post-Deployment Tasks

1. **Monitor for 24 hours**: Check logs daily
2. **Verify data integrity**: Confirm snapshots append correctly
3. **Check file growth**: Should be ~620 KB per day
4. **Remove old backups** (after 1 week of stable operation):
   ```bash
   rm data/*.backup.*
   rm data/*.pre-*
   ```

## 📞 Support

If issues arise:
- Check `RECOVERY_SUMMARY.md` for technical details
- Review `CLAUDE.md` for architecture documentation
- Railway logs show real-time errors
- All backups preserved in `data/` directory

---

**Ready to deploy!** 🚀

Follow steps 1-4 above to push to Railway.
