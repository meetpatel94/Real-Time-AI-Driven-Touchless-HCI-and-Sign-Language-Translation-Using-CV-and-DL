import { AlertTriangle, Clock, MapPin } from "lucide-react";

const alerts = [
  {
    id: 1,
    type: "Watchlist Match",
    plate: "GJ01AB1234",
    camera: "C-038",
    location: "Gift City Road",
    time: "10:44:03 AM",
    severity: "critical",
    color: "red",
  },
  {
    id: 2,
    type: "Speed Violation",
    plate: "GJ05JK6789",
    camera: "C-115",
    location: "S.G. Highway",
    time: "10:38:12 AM",
    severity: "high",
    color: "orange",
  },
  {
    id: 3,
    type: "Wrong Direction",
    plate: "GJ18CD4521",
    camera: "C-001",
    location: "Shahibaug Road",
    time: "10:21:45 AM",
    severity: "medium",
    color: "yellow",
  },
  {
    id: 4,
    type: "Crowd Detected",
    plate: "—",
    camera: "C-207",
    location: "Vadodara Center",
    time: "10:15:33 AM",
    severity: "low",
    color: "blue",
  },
];

const colorMap: any = {
  red: {
    border: "border-l-[#ef4444]",
    bg: "bg-[#1a1212]",
    iconBg: "bg-[#2a1515] border-[#5a1f1f]",
    iconColor: "text-[#ef4444]",
    badge: "bg-[#3a1515] text-[#fca5a5] border-[#5a1f1f]",
  },
  orange: {
    border: "border-l-[#f97316]",
    bg: "bg-[#1a150f]",
    iconBg: "bg-[#2a1f0f] border-[#5a3a14]",
    iconColor: "text-[#f97316]",
    badge: "bg-[#2a1f0f] text-[#fdba74] border-[#5a3a14]",
  },
  yellow: {
    border: "border-l-[#eab308]",
    bg: "bg-[#1a190f]",
    iconBg: "bg-[#2a2510] border-[#5a4a14]",
    iconColor: "text-[#eab308]",
    badge: "bg-[#2a2510] text-[#fde68a] border-[#5a4a14]",
  },
  blue: {
    border: "border-l-[#3b82f6]",
    bg: "bg-[#10141f]",
    iconBg: "bg-[#111a33] border-[#1e3a6a]",
    iconColor: "text-[#3b82f6]",
    badge: "bg-[#111a33] text-[#93c5fd] border-[#1e3a6a]",
  },
};

export default function RecentAlerts() {
  return (
    <div className="panel rounded-[10px] overflow-hidden flex flex-col">
      <div className="panel-header h-[36px] flex items-center justify-between px-[12px] shrink-0">
        <div className="flex items-center gap-[8px]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#ef4444] animate-pulse shadow-[0_0_6px_#ef4444]" />
          <span className="text-[10px] font-bold tracking-[0.14em] uppercase text-white">Recent Alerts</span>
          <span className="text-[9px] px-[5px] py-[1px] rounded bg-[#2a1515] border border-[#5a1f1f] text-[#fca5a5] font-bold">12 NEW</span>
        </div>
        <button className="text-[10px] font-semibold text-[#7aa2d6] hover:text-white transition-colors">View All</button>
      </div>

      <div className="p-[8px] space-y-[6px]">
        {alerts.map((a) => {
          const c = colorMap[a.color];
          return (
            <div
              key={a.id}
              className={`rounded-[6px] border border-[#1a2c52] ${c.bg} ${c.border} border-l-[3px] p-[8px] hover:border-[#2a4a8a] transition-colors cursor-pointer group`}
            >
              <div className="flex items-start gap-[8px]">
                <div className={`w-[26px] h-[26px] rounded-[6px] border flex items-center justify-center shrink-0 ${c.iconBg}`}>
                  <AlertTriangle className={`w-[12px] h-[12px] ${c.iconColor}`} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-[6px]">
                    <span className="text-[11px] font-bold text-white tracking-[0.01em]">{a.type}</span>
                    {a.plate !== "—" && (
                      <span className="text-[9px] font-mono font-bold px-[4px] py-[1px] rounded bg-[#0a1123] border border-[#1a2c52] text-[#cbd5e1]">
                        {a.plate}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-[8px] mt-[4px]">
                    <span className="flex items-center gap-[3px] text-[9px] text-[#7a94c0]">
                      <MapPin className="w-[9px] h-[9px]" />
                      {a.camera} • {a.location}
                    </span>
                    <span className="flex items-center gap-[3px] text-[9px] text-[#5a77a8] font-mono">
                      <Clock className="w-[9px] h-[9px]" />
                      {a.time}
                    </span>
                  </div>
                </div>
                <div className={`text-[8px] font-bold px-[5px] py-[2px] rounded-full border ${c.badge} uppercase tracking-wide`}>
                  {a.severity}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
