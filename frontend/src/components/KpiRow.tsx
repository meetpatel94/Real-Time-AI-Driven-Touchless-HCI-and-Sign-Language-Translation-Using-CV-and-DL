import { Video, Car, AlertTriangle, ShieldCheck, Users } from "lucide-react";

const kpis = [
  {
    label: "Total Cameras",
    value: "12,842",
    sub: "Online: 11,243 (87%)",
    subColor: "text-[#4ade80]",
    icon: Video,
    iconBg: "bg-[#122a52] border-[#1e3f7a]",
    iconColor: "text-[#60a5fa]",
    dot: "bg-[#22c55e]",
  },
  {
    label: "Vehicles Detected (Today)",
    value: "18,729",
    sub: "↑ 12.5% from yesterday",
    subColor: "text-[#4ade80]",
    icon: Car,
    iconBg: "bg-[#102a1f] border-[#1a4a32]",
    iconColor: "text-[#4ade80]",
    dot: "bg-[#22c55e]",
  },
  {
    label: "Alerts (Today)",
    value: "23",
    sub: "↑ 8 from yesterday",
    subColor: "text-[#fb923c]",
    icon: AlertTriangle,
    iconBg: "bg-[#2a1f0f] border-[#5a3a14]",
    iconColor: "text-[#fb923c]",
    dot: "bg-[#f97316]",
  },
  {
    label: "Watchlist Matches",
    value: "7",
    sub: "Active Alerts",
    subColor: "text-[#f87171]",
    icon: ShieldCheck,
    iconBg: "bg-[#2a1313] border-[#5a1f1f]",
    iconColor: "text-[#f87171]",
    dot: "bg-[#ef4444]",
  },
  {
    label: "Active Users",
    value: "56",
    sub: "Online",
    subColor: "text-[#a78bfa]",
    icon: Users,
    iconBg: "bg-[#1e1633] border-[#3a2a6a]",
    iconColor: "text-[#a78bfa]",
    dot: "bg-[#8b5cf6]",
  },
];

export default function KpiRow() {
  return (
    <div className="grid grid-cols-5 gap-[12px]">
      {kpis.map((k) => (
        <div
          key={k.label}
          className="panel rounded-[10px] p-[12px] flex items-center gap-[12px] relative overflow-hidden group hover:border-[#2a4a8a] transition-colors"
        >
          <div className="absolute inset-0 bg-gradient-to-br from-white/[0.02] to-transparent pointer-events-none" />
          <div
            className={`w-[42px] h-[42px] rounded-[10px] border flex items-center justify-center shrink-0 ${k.iconBg} shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]`}
          >
            <k.icon className={`w-[20px] h-[20px] ${k.iconColor}`} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[9px] font-bold tracking-[0.12em] uppercase text-[#6b84b3] leading-none mb-[4px]">
              {k.label}
            </div>
            <div className="flex items-baseline gap-[8px]">
              <div className="text-[22px] font-bold tracking-[-0.02em] text-white leading-none">{k.value}</div>
              <div className={`w-[6px] h-[6px] rounded-full ${k.dot} shadow-[0_0_6px_currentColor]`} />
            </div>
            <div className={`text-[10.5px] font-medium mt-[4px] ${k.subColor} leading-none`}>{k.sub}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
