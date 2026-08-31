import {
  LayoutDashboard,
  Video,
  Map,
  Search,
  ShieldAlert,
  Bell,
  BarChart3,
  FileSearch,
  HeartPulse,
  FileText,
  Users,
  Settings,
} from "lucide-react";

const navItems = [
  { icon: LayoutDashboard, label: "Dashboard", active: true },
  { icon: Video, label: "Live View" },
  { icon: Map, label: "Camera Map" },
  { icon: Search, label: "Vehicle Search" },
  { icon: ShieldAlert, label: "Watchlist" },
  { icon: Bell, label: "Alerts", badge: "12" },
  { icon: BarChart3, label: "Analytics" },
  { icon: FileSearch, label: "Investigation" },
  { icon: HeartPulse, label: "Camera Health" },
  { icon: FileText, label: "Reports" },
  { icon: Users, label: "Users & Roles" },
  { icon: Settings, label: "System Settings" },
];

export default function Sidebar() {
  return (
    <aside className="fixed left-0 top-0 bottom-0 w-[200px] bg-[#0a1123] border-r border-[#1a2c52] flex flex-col z-50 overflow-hidden">
      {/* Logo Header */}
      <div className="px-[14px] pt-[14px] pb-[12px] border-b border-[#1a2c52]/80">
        <div className="flex items-center gap-[10px]">
          <div className="w-[36px] h-[36px] rounded-full bg-gradient-to-br from-[#1e3a8a] to-[#0f1e4a] border border-[#2a4a8a] flex items-center justify-center shadow-[0_0_12px_rgba(59,130,246,0.3)] shrink-0">
            <div className="w-[26px] h-[26px] rounded-full bg-[#0a1123] border border-[#2a4a8a]/50 flex items-center justify-center">
              <span className="text-[11px] font-black tracking-widest text-[#60a5fa]">GP</span>
            </div>
          </div>
          <div className="leading-[1.1]">
            <div className="text-[13px] font-bold tracking-[0.02em] text-white">Gujarat Police</div>
            <div className="text-[8px] font-semibold tracking-[0.12em] uppercase text-[#7aa2d6] mt-[2px] leading-[1.2]">
              Unified AI CCTV Intelligence Platform
            </div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-[8px] py-[10px] space-y-[2px] overflow-y-auto">
        {navItems.map((item) => (
          <div
            key={item.label}
            className={`group flex items-center gap-[10px] px-[10px] h-[32px] rounded-[6px] cursor-pointer transition-all text-[12.5px] ${
              item.active
                ? "bg-[#12224a] border border-[#244078] text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_0_10px_rgba(37,99,235,0.15)]"
                : "text-[#8aa0c8] hover:bg-[#111e3a] hover:text-[#c5d5f0] border border-transparent"
            }`}
          >
            <item.icon
              className={`w-[15px] h-[15px] shrink-0 ${
                item.active ? "text-[#60a5fa]" : "text-[#5a77a8] group-hover:text-[#8fb0e0]"
              }`}
            />
            <span className="flex-1 truncate font-[500] tracking-[0.01em]">{item.label}</span>
            {item.badge && (
              <span className="bg-[#dc2626] text-white text-[10px] font-bold px-[5px] h-[16px] min-w-[18px] flex items-center justify-center rounded-full leading-none">
                {item.badge}
              </span>
            )}
          </div>
        ))}
      </nav>

      {/* System Status */}
      <div className="px-[10px] pb-[10px]">
        <div className="rounded-[8px] bg-[#0e1a33] border border-[#1c335a] p-[10px]">
          <div className="text-[9px] font-bold tracking-[0.14em] uppercase text-[#5f7eb3] mb-[8px]">
            System Status
          </div>
          <div className="space-y-[6px]">
            {[
              { label: "All Systems", value: "Operational" },
              { label: "AI Engine", value: "Operational" },
              { label: "Storage", value: "Operational" },
              { label: "Network", value: "Good" },
            ].map((s) => (
              <div key={s.label} className="flex items-center justify-between">
                <div className="flex items-center gap-[6px]">
                  <div className="w-[5px] h-[5px] rounded-full bg-[#22c55e] shadow-[0_0_6px_rgba(34,197,94,0.6)]" />
                  <span className="text-[10.5px] text-[#8aa0c8]">{s.label}</span>
                </div>
                <span className="text-[9px] font-semibold px-[6px] py-[1px] rounded-[10px] bg-[#102a1a] text-[#4ade80] border border-[#1a4a2a]">
                  {s.value}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-[10px] text-[9.5px] text-[#4a5f86] text-center tracking-wide">© 2026 Gujarat Police</div>
      </div>
    </aside>
  );
}
