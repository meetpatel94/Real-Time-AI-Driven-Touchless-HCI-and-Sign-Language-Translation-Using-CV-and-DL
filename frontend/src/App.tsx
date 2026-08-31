import Sidebar from "./components/Sidebar";
import Header from "./components/Header";
import KpiRow from "./components/KpiRow";
import LiveFeeds from "./components/LiveFeeds";
import GisMap from "./components/GisMap";
import RecentAlerts from "./components/RecentAlerts";
import CameraHealth from "./components/CameraHealth";
import VehicleSearch from "./components/VehicleSearch";
import JourneyTimeline from "./components/JourneyTimeline";
import AiAnalytics from "./components/AiAnalytics";

function App() {
  return (
    <div className="min-h-screen bg-[#060a17] text-white selection:bg-[#1e3a8a]/50 overflow-x-hidden">
      <Sidebar />
      <div className="ml-[200px] min-h-screen flex flex-col">
        <Header />
        <main className="p-[10px] flex flex-col gap-[10px] bg-[#060a17] flex-1">
          {/* KPI ROW */}
          <KpiRow />

          {/* MAIN 3-COLUMN */}
          <div className="grid grid-cols-[34%_39.5%_26.5%] gap-[10px] items-start">
            {/* LEFT */}
            <div className="flex flex-col h-full">
              <LiveFeeds />
            </div>

            {/* CENTER */}
            <div className="flex flex-col h-full min-h-[380px]">
              <GisMap />
            </div>

            {/* RIGHT */}
            <div className="flex flex-col">
              <RecentAlerts />
              <CameraHealth />
            </div>
          </div>

          {/* BOTTOM INTELLIGENCE ROW */}
          <div className="grid grid-cols-[20%_47%_33%] gap-[10px] items-stretch">
            <div className="min-h-[340px]">
              <VehicleSearch />
            </div>
            <div className="min-h-[340px]">
              <JourneyTimeline />
            </div>
            <div className="min-h-[340px]">
              <AiAnalytics />
            </div>
          </div>

          {/* footer spacing */}
          <div className="h-[8px]" />
        </main>
      </div>

      {/* subtle vignette / glow */}
      <div className="pointer-events-none fixed inset-0 ml-[200px] bg-[radial-gradient(ellipse_at_top,_rgba(59,130,246,0.06),_transparent_60%)]" />
    </div>
  );
}

export default App;
