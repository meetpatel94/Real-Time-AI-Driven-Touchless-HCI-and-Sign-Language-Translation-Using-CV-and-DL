export default function AiAnalytics() {
  const data = [
    { label: "Vehicle Count", value: 18729, color: "bg-[#3b82f6]", width: "100%" },
    { label: "Two Wheeler", value: 9642, color: "bg-[#22c55e]", width: "51%" },
    { label: "Heavy Vehicle", value: 2153, color: "bg-[#f59e0b]", width: "22%" },
    { label: "Pedestrians", value: 6892, color: "bg-[#8b5cf6]", width: "42%" },
  ];

  const max = 18729;

  return (
    <div className="panel rounded-[10px] overflow-hidden flex flex-col h-full">
      <div className="panel-header h-[36px] flex items-center justify-between px-[12px] shrink-0">
        <div className="flex items-center gap-[8px]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#8b5cf6] shadow-[0_0_6px_#8b5cf6]" />
          <span className="text-[10px] font-bold tracking-[0.14em] uppercase text-white">AI Analytics (Today)</span>
        </div>
        <button className="text-[10px] font-semibold text-[#7aa2d6] hover:text-white transition-colors">View Report</button>
      </div>

      <div className="p-[12px] flex-1 flex flex-col">
        {/* chart */}
        <div className="flex-1 flex items-end gap-[14px] px-[8px] pt-[12px] pb-[8px] bg-[#0a1123] rounded-[8px] border border-[#1a2c52] min-h-[180px]">
          {data.map((d) => (
            <div key={d.label} className="flex-1 flex flex-col items-center gap-[8px] h-full justify-end">
              <div className="text-[11px] font-bold text-white">{d.value.toLocaleString()}</div>
              <div className="w-full flex justify-center items-end h-[120px]">
                <div
                  className={`w-[70%] rounded-t-[6px] ${d.color} shadow-[0_0_12px_currentColor] relative overflow-hidden group hover:brightness-110 transition-all`}
                  style={{ height: `${(d.value / max) * 100}%`, minHeight: "24px" }}
                >
                  <div className="absolute inset-0 bg-gradient-to-t from-black/30 to-white/10" />
                  <div className="absolute top-0 left-0 right-0 h-[1px] bg-white/30" />
                </div>
              </div>
              <div className="text-[9px] font-semibold text-[#8aa0c8] text-center leading-[1.1] uppercase tracking-wide">
                {d.label.split(" ").map((w, i) => (
                  <div key={i}>{w}</div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* stats */}
        <div className="mt-[10px] grid grid-cols-2 gap-[6px]">
          <div className="h-[36px] rounded-[6px] bg-[#0e1a33] border border-[#1e355e] flex items-center gap-[8px] px-[10px]">
            <div className="w-[24px] h-[24px] rounded-[6px] bg-[#122a52] border border-[#1e3f7a] flex items-center justify-center">
              <span className="text-[12px]">🚗</span>
            </div>
            <div>
              <div className="text-[10px] font-bold text-white leading-none">Peak Hour</div>
              <div className="text-[9px] text-[#7a94c0] leading-none mt-[2px]">09:30 AM - 412 veh</div>
            </div>
          </div>
          <div className="h-[36px] rounded-[6px] bg-[#0e1a33] border border-[#1e355e] flex items-center gap-[8px] px-[10px]">
            <div className="w-[24px] h-[24px] rounded-[6px] bg-[#1a2a1f] border border-[#2a4a2a] flex items-center justify-center">
              <span className="text-[12px]">📈</span>
            </div>
            <div>
              <div className="text-[10px] font-bold text-white leading-none">Growth</div>
              <div className="text-[9px] text-[#4ade80] leading-none mt-[2px]">↑ 12.5% vs yesterday</div>
            </div>
          </div>
        </div>

        <div className="mt-[10px] h-[28px] rounded-[6px] bg-[#0a1123] border border-[#1a2c52] flex items-center px-[10px] gap-[8px]">
          <div className="flex items-center gap-[4px]">
            <div className="w-[8px] h-[8px] rounded-[2px] bg-[#3b82f6]" />
            <span className="text-[8px] text-[#5a77a8]">Vehicle</span>
          </div>
          <div className="flex items-center gap-[4px]">
            <div className="w-[8px] h-[8px] rounded-[2px] bg-[#22c55e]" />
            <span className="text-[8px] text-[#5a77a8]">2W</span>
          </div>
          <div className="flex items-center gap-[4px]">
            <div className="w-[8px] h-[8px] rounded-[2px] bg-[#f59e0b]" />
            <span className="text-[8px] text-[#5a77a8]">Heavy</span>
          </div>
          <div className="flex items-center gap-[4px]">
            <div className="w-[8px] h-[8px] rounded-[2px] bg-[#8b5cf6]" />
            <span className="text-[8px] text-[#5a77a8]">Ped</span>
          </div>
          <div className="ml-auto flex items-center gap-[4px]">
            <div className="w-[4px] h-[4px] rounded-full bg-[#22c55e] animate-pulse" />
            <span className="text-[8px] text-[#5a77a8]">AI Engine • Real-time</span>
          </div>
        </div>
      </div>
    </div>
  );
}
