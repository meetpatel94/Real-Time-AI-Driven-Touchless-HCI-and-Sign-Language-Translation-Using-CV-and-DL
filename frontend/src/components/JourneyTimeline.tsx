const steps = [
  {
    id: "C-001",
    time: "10:21:15 AM",
    location: "Shahibaug Road",
    city: "Ahmedabad",
    img: "https://images.unsplash.com/photo-1449824913935-59a10b8d2000?w=200&h=120&fit=crop&crop=center",
  },
  {
    id: "C-007",
    time: "10:28:42 AM",
    location: "Naranpura Road",
    city: "Ahmedabad",
    img: "https://images.unsplash.com/photo-1494783367193-149034c05e8f?w=200&h=120&fit=crop&crop=center",
  },
  {
    id: "C-015",
    time: "10:34:18 AM",
    location: "Kudasan Road",
    city: "Gandhinagar",
    img: "https://images.unsplash.com/photo-1465447142348-e9952c393450?w=200&h=120&fit=crop&crop=center",
  },
  {
    id: "C-038",
    time: "10:44:03 AM",
    location: "Gift City Road",
    city: "Gandhinagar",
    img: "https://images.unsplash.com/photo-1519003722824-19424363323d?w=200&h=120&fit=crop&crop=center",
    alert: true,
  },
];

export default function JourneyTimeline() {
  return (
    <div className="panel rounded-[10px] overflow-hidden flex flex-col h-full">
      <div className="panel-header h-[36px] flex items-center justify-between px-[12px] shrink-0">
        <div className="flex items-center gap-[8px]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#3b82f6] shadow-[0_0_6px_#3b82f6]" />
          <span className="text-[10px] font-bold tracking-[0.14em] uppercase text-white">Vehicle Journey Timeline</span>
          <span className="text-[9px] px-[5px] py-[1px] rounded bg-[#122a52] border border-[#1e3f7a] text-[#60a5fa] font-mono font-bold">GJ01AB1234</span>
        </div>
        <div className="flex items-center gap-[8px]">
          <span className="text-[9px] text-[#5a77a8]">4 sightings • 22 min</span>
          <button className="text-[10px] font-semibold text-[#7aa2d6] hover:text-white transition-colors">Export</button>
        </div>
      </div>

      <div className="p-[14px] flex-1 flex flex-col justify-center">
        {/* Timeline line */}
        <div className="relative">
          {/* horizontal line */}
          <div className="absolute top-[56px] left-[40px] right-[40px] h-[2px] bg-[#1a2c52]" />
          <div className="absolute top-[56px] left-[40px] right-[40px] h-[2px] bg-gradient-to-r from-[#3b82f6] via-[#3b82f6] to-[#ef4444] opacity-60" />
          {/* blue dotted overlay */}
          <div className="absolute top-[55px] left-[40px] right-[40px] h-[4px] flex items-center">
            <div className="w-full h-[2px] border-t-[2px] border-dashed border-[#3b82f6]/50" />
          </div>

          <div className="grid grid-cols-4 gap-[12px] relative">
            {steps.map((s, idx) => (
              <div key={s.id} className="flex flex-col items-center">
                {/* image */}
                <div
                  className={`relative w-full h-[86px] rounded-[8px] overflow-hidden border ${
                    s.alert ? "border-[#ef4444] shadow-[0_0_12px_rgba(239,68,68,0.3)]" : "border-[#1a2c52]"
                  } bg-[#080c1a] group`}
                >
                  <img src={s.img} alt={s.location} className="w-full h-full object-cover" />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent" />
                  <div className="absolute top-[6px] left-[6px] bg-black/60 backdrop-blur px-[5px] h-[16px] rounded-[4px] border border-white/10 flex items-center gap-[4px]">
                    <div className={`w-[4px] h-[4px] rounded-full ${s.alert ? "bg-[#ef4444]" : "bg-[#22c55e]"}`} />
                    <span className="text-[8px] font-bold text-white tracking-wide">{s.id}</span>
                  </div>
                  {s.alert && (
                    <div className="absolute top-[6px] right-[6px] bg-[#dc2626] px-[5px] h-[16px] rounded-[4px] flex items-center">
                      <span className="text-[7px] font-black tracking-widest text-white">ALERT</span>
                    </div>
                  )}
                  <div className="absolute bottom-[4px] left-[6px] right-[6px] flex justify-between items-center">
                    <span className="text-[8px] font-mono text-white/80">{s.time}</span>
                    <div className="w-[12px] h-[12px] rounded-full bg-white/20 backdrop-blur flex items-center justify-center">
                      <div className="w-[4px] h-[4px] rounded-full bg-white" />
                    </div>
                  </div>
                </div>

                {/* node */}
                <div className="relative mt-[10px]">
                  <div
                    className={`w-[28px] h-[28px] rounded-full border-2 flex items-center justify-center text-[11px] font-bold shadow-lg z-10 relative ${
                      s.alert
                        ? "bg-[#dc2626] border-white text-white shadow-[0_0_12px_rgba(220,38,38,0.5)]"
                        : idx === 0
                        ? "bg-[#1d4ed8] border-white text-white shadow-[0_0_10px_rgba(59,130,246,0.4)]"
                        : "bg-[#0e1a33] border-[#3b82f6] text-[#60a5fa]"
                    }`}
                  >
                    {idx + 1}
                  </div>
                  {s.alert && <div className="absolute inset-0 rounded-full bg-[#ef4444] animate-ping opacity-30" />}
                </div>

                {/* arrow between */}
                {idx < steps.length - 1 && (
                  <div className="absolute top-[130px] hidden xl:block" style={{ left: `calc(${(idx + 1) * 25}% - 8px)` }}>
                    <div className="text-[#3b82f6] text-[12px] font-bold">→</div>
                  </div>
                )}

                {/* info */}
                <div className="mt-[8px] text-center">
                  <div className="text-[11px] font-bold text-white leading-none">{s.id}</div>
                  <div className="text-[10px] text-[#cbd5e1] mt-[2px] leading-[1.1]">{s.location}</div>
                  <div className="text-[9px] text-[#5a77a8] leading-none mt-[1px]">{s.city}</div>
                  <div className="text-[9px] font-mono text-[#7aa2d6] mt-[4px] bg-[#0a1123] border border-[#1a2c52] px-[6px] py-[2px] rounded-full inline-block">
                    {s.time}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* bottom stats */}
        <div className="mt-[16px] grid grid-cols-4 gap-[8px]">
          <div className="h-[32px] rounded-[6px] bg-[#0a1123] border border-[#1a2c52] flex items-center justify-between px-[10px]">
            <span className="text-[9px] text-[#5a77a8] uppercase tracking-wide font-semibold">Distance</span>
            <span className="text-[11px] font-bold text-white">18.4 km</span>
          </div>
          <div className="h-[32px] rounded-[6px] bg-[#0a1123] border border-[#1a2c52] flex items-center justify-between px-[10px]">
            <span className="text-[9px] text-[#5a77a8] uppercase tracking-wide font-semibold">Duration</span>
            <span className="text-[11px] font-bold text-white">22m 48s</span>
          </div>
          <div className="h-[32px] rounded-[6px] bg-[#0a1123] border border-[#1a2c52] flex items-center justify-between px-[10px]">
            <span className="text-[9px] text-[#5a77a8] uppercase tracking-wide font-semibold">Avg Speed</span>
            <span className="text-[11px] font-bold text-white">48 km/h</span>
          </div>
          <div className="h-[32px] rounded-[6px] bg-[#1a1212] border border-[#5a1f1f] flex items-center justify-between px-[10px]">
            <span className="text-[9px] text-[#fca5a5] uppercase tracking-wide font-semibold">Status</span>
            <span className="text-[11px] font-bold text-[#ef4444] flex items-center gap-[4px]">
              <span className="w-[5px] h-[5px] rounded-full bg-[#ef4444] animate-pulse" />
              ALERT ACTIVE
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
