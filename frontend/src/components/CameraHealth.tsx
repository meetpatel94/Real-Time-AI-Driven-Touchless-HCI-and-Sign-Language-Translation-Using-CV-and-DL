export default function CameraHealth() {
  const online = 11243;
  const offline = 1128;
  const poor = 471;
  const total = online + offline + poor;
  const onlinePct = (online / total) * 100;
  const offlinePct = (offline / total) * 100;

  // donut chart using conic-gradient
  const donutStyle = {
    background: `conic-gradient(
      #22c55e 0% ${onlinePct}%,
      #ef4444 ${onlinePct}% ${onlinePct + offlinePct}%,
      #f59e0b ${onlinePct + offlinePct}% 100%
    )`,
  };

  return (
    <div className="panel rounded-[10px] overflow-hidden flex flex-col mt-[12px]">
      <div className="panel-header h-[36px] flex items-center justify-between px-[12px] shrink-0">
        <div className="flex items-center gap-[8px]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#22c55e] shadow-[0_0_6px_#22c55e]" />
          <span className="text-[10px] font-bold tracking-[0.14em] uppercase text-white">Camera Health</span>
        </div>
        <button className="text-[10px] font-semibold text-[#7aa2d6] hover:text-white transition-colors">View All</button>
      </div>

      <div className="p-[12px] flex items-center gap-[16px]">
        {/* Donut */}
        <div className="relative w-[92px] h-[92px] shrink-0">
          <div className="absolute inset-0 rounded-full" style={donutStyle} />
          <div className="absolute inset-[10px] rounded-full bg-[#0f1a32] border border-[#1a2c52] flex flex-col items-center justify-center">
            <span className="text-[18px] font-bold text-white leading-none">{total.toLocaleString()}</span>
            <span className="text-[8px] font-semibold tracking-[0.12em] uppercase text-[#5a77a8] mt-[2px]">Total</span>
          </div>
          {/* glow */}
          <div className="absolute inset-0 rounded-full shadow-[inset_0_0_20px_rgba(0,0,0,0.5)] pointer-events-none" />
        </div>

        {/* Legend */}
        <div className="flex-1 space-y-[8px]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-[6px]">
              <div className="w-[8px] h-[8px] rounded-[2px] bg-[#22c55e] shadow-[0_0_6px_rgba(34,197,94,0.5)]" />
              <span className="text-[11px] text-[#cbd5e1]">Online</span>
            </div>
            <div className="text-right">
              <div className="text-[11px] font-bold text-white">11,243 (87%)</div>
              <div className="w-[64px] h-[4px] rounded-full bg-[#0a1123] border border-[#1a2c52] overflow-hidden mt-[2px]">
                <div className="h-full bg-[#22c55e]" style={{ width: "87%" }} />
              </div>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-[6px]">
              <div className="w-[8px] h-[8px] rounded-[2px] bg-[#ef4444] shadow-[0_0_6px_rgba(239,68,68,0.5)]" />
              <span className="text-[11px] text-[#cbd5e1]">Offline</span>
            </div>
            <div className="text-right">
              <div className="text-[11px] font-bold text-white">1,128 (9%)</div>
              <div className="w-[64px] h-[4px] rounded-full bg-[#0a1123] border border-[#1a2c52] overflow-hidden mt-[2px]">
                <div className="h-full bg-[#ef4444]" style={{ width: "9%" }} />
              </div>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-[6px]">
              <div className="w-[8px] h-[8px] rounded-[2px] bg-[#f59e0b] shadow-[0_0_6px_rgba(245,158,11,0.5)]" />
              <span className="text-[11px] text-[#cbd5e1]">Poor Signal</span>
            </div>
            <div className="text-right">
              <div className="text-[11px] font-bold text-white">471 (4%)</div>
              <div className="w-[64px] h-[4px] rounded-full bg-[#0a1123] border border-[#1a2c52] overflow-hidden mt-[2px]">
                <div className="h-full bg-[#f59e0b]" style={{ width: "20%" }} />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="px-[12px] pb-[10px]">
        <div className="h-[24px] rounded-[6px] bg-[#0a1123] border border-[#1a2c52] flex items-center px-[8px] gap-[6px]">
          <div className="w-[4px] h-[4px] rounded-full bg-[#22c55e] animate-pulse" />
          <span className="text-[9px] text-[#7a94c0]">Last checked: 10:44:12 AM • Auto-refresh every 30s</span>
        </div>
      </div>
    </div>
  );
}
