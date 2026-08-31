# Gujarat Police — Unified AI CCTV Intelligence Platform (Frontend)

Desktop-first command-center dashboard built with **React + TypeScript + Tailwind CSS v4** + **Lucide Icons**.

## Features Implemented (Dashboard Only)

**Layout Composition (matches reference extremely closely):**
- Fixed left sidebar 200px: Gujarat Police emblem, title, subtitle, 12 nav items in exact order with Alerts badge `12`, System Status (All Systems / AI Engine / Storage / Network) and © 2026.
- Top header to the right of sidebar: centered wide search `Search Vehicle / Camera / Location…`, notification bell with red `12`, settings icon, Inspector Rajveer avatar + Gandhinagar Command.
- Five-card KPI row equal width: Total Cameras 12,842 Online 11,243 (87%), Vehicles Detected 18,729 ↑12.5%, Alerts 23 ↑8, Watchlist Matches 7 Active Alerts, Active Users 56 Online — with blue/green/orange/red/purple accents.
- Main three-column area:
  - **LEFT 34% — LIVE CCTV FEEDS**: 2x2 grid, 4 cards C-001 Shahibaug Road Ahmedabad, C-038 Gift City Road Gandhinagar (ALERT), C-115 S.G. Highway Ahmedabad, C-207 Vadodara City Center, each with green LIVE badge, status, video controls, realistic traffic imagery.
  - **CENTER ~40% — GIS CAMERA MAP**: Dark-blue Ahmedabad/Gandhinagar map with road names, city labels, green camera markers, orange warning, red critical C-038, blue dashed route with numbered points 1-4, zoom/layer/filter controls, red floating alert popup (GJ01AB1234, C-038, Gift City Road, 10:44:03 AM, View Details).
  - **RIGHT ~27% — RECENT ALERTS + CAMERA HEALTH**: Recent Alerts with View All and 4 compact cards (Watchlist Match GJ01AB1234 red, Speed Violation GJ05JK6789 orange, Wrong Direction GJ18CD4521 yellow, Crowd Detected blue) with camera/location/time. Camera Health donut chart Online 11,243 (87%) Offline 1,128 (9%) Poor Signal 471 (4%) + legend.
- Bottom intelligence row:
  - **LEFT 20% — VEHICLE SEARCH**: plate input GJ01AB1234, blue Search button, vehicle snapshot, large plate text, red outlined WATCHLIST MATCH badge, White Swift Dzire, Color White, First Seen 10:21:15 AM Last Seen 10:44:03 AM.
  - **CENTER 47% — VEHICLE JOURNEY TIMELINE**: horizontal 4-step C-001 → C-007 → C-015 → C-038 with nodes, timestamps 10:21:15 AM / 10:28:42 AM / 10:34:18 AM / 10:44:03 AM, locations Shahibaug/Naranpura/Kudasan/Gift City, CCTV snapshots, final ALERT highlighted red + stats distance/duration/avg speed.
  - **RIGHT 33% — AI ANALYTICS (Today)**: View Report, dark bar chart Vehicle Count 18,729 Two Wheeler 9,642 Heavy Vehicle 2,153 Pedestrians 6,892 with values above bars.

**Design System:**
- Full-screen dark navy/near-black `#060a17`, panels `#111c36`→`#0f1a32`, border `#1c2f55`, thin blue-gray borders, subtle blue/cyan glow, white/gray typography, compact spacing, small uppercase labels, green online, orange warning, red critical, purple accents.
- Dense, information-rich, aligned to consistent grid, desktop viewport primary.

## Tech Stack
- Vite + React 19 + TypeScript
- Tailwind CSS v4 (`@tailwindcss/vite`)
- Lucide React icons
- Realistic mock data, frontend-only, reusable components ready for RTSP/API/WebSocket integration.

## Project Structure
```
src/
  components/
    Sidebar.tsx
    Header.tsx
    KpiRow.tsx
    LiveFeeds.tsx
    GisMap.tsx
    RecentAlerts.tsx
    CameraHealth.tsx
    VehicleSearch.tsx
    JourneyTimeline.tsx
    AiAnalytics.tsx
  App.tsx
  main.tsx
  index.css
```

## Run
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
npm run build
```

## Next Steps (not in this PR)
- RTSP/WebRTC live player integration
- Mapbox/Leaflet GIS with real camera GeoJSON
- Vehicle search API, WebSocket alerts
- Other pages: Live View, Camera Map, Watchlist, etc.

## Preview
The dashboard is optimized for 1920x1080 command-center displays with minimal empty space and premium police console feel.
