import { Layers, Filter, ZoomIn, ZoomOut, Crosshair, AlertTriangle } from "lucide-react";

export default function GisMap() {
  return (
    <div className="panel rounded-[10px] overflow-hidden flex flex-col h-full relative">
      <div className="panel-header h-[36px] flex items-center justify-between px-[12px] shrink-0 z-10">
        <div className="flex items-center gap-[8px]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#3b82f6] shadow-[0_0_6px_#3b82f6]" />
          <span className="text-[10px] font-bold tracking-[0.14em] uppercase text-white">GIS Camera Map</span>
          <span className="text-[9px] px-[5px] py-[1px] rounded bg-[#122a52] border border-[#1e3f7a] text-[#93c5fd] font-semibold">AHMEDABAD • GANDHINAGAR</span>
        </div>
        <div className="flex items-center gap-[6px]">
          <button className="w-[22px] h-[22px] rounded-[5px] bg-[#0e1a33] border border-[#1e355e] flex items-center justify-center hover:bg-[#162a52]">
            <Layers className="w-[12px] h-[12px] text-[#7aa2d6]" />
          </button>
          <button className="w-[22px] h-[22px] rounded-[5px] bg-[#0e1a33] border border-[#1e355e] flex items-center justify-center hover:bg-[#162a52]">
            <Filter className="w-[12px] h-[12px] text-[#7aa2d6]" />
          </button>
          <button className="text-[10px] font-semibold text-[#7aa2d6] hover:text-white transition-colors ml-[4px]">View All</button>
        </div>
      </div>

      {/* Map Canvas */}
      <div className="relative flex-1 bg-[#070e22] overflow-hidden min-h-[320px]">
        {/* base dark map */}
        <div className="absolute inset-0">
          {/* subtle grid */}
          <div
            className="absolute inset-0 opacity-[0.04]"
            style={{
              backgroundImage:
                "linear-gradient(#3b82f6 1px, transparent 1px), linear-gradient(90deg, #3b82f6 1px, transparent 1px)",
              backgroundSize: "32px 32px",
            }}
          />
          {/* roads - SVG */}
          <svg className="absolute inset-0 w-full h-full" viewBox="0 0 600 380" preserveAspectRatio="none">
            {/* main highways */}
            <path d="M 0 120 Q 150 115 300 130 T 600 140" stroke="#1e3a5f" strokeWidth="3" fill="none" />
            <path d="M 0 180 Q 200 175 350 190 T 600 210" stroke="#1e3a5f" strokeWidth="2.5" fill="none" />
            <path d="M 0 250 Q 180 245 320 260 T 600 280" stroke="#1e3a5f" strokeWidth="2" fill="none" />
            {/* vertical roads */}
            <path d="M 120 0 Q 125 120 130 380" stroke="#1a3355" strokeWidth="2" fill="none" />
            <path d="M 280 0 Q 285 150 290 380" stroke="#1a3355" strokeWidth="2.5" fill="none" />
            <path d="M 450 0 Q 445 180 440 380" stroke="#1e3a5f" strokeWidth="3" fill="none" />
            {/* blue route - vehicle journey */}
            <path
              d="M 90 110 Q 150 125 230 145 Q 300 170 380 200 Q 430 220 480 235"
              stroke="#3b82f6"
              strokeWidth="2.5"
              fill="none"
              strokeDasharray="6 4"
              className="drop-shadow-[0_0_6px_rgba(59,130,246,0.6)]"
            />
            {/* route glow */}
            <path
              d="M 90 110 Q 150 125 230 145 Q 300 170 380 200 Q 430 220 480 235"
              stroke="#60a5fa"
              strokeWidth="8"
              fill="none"
              opacity="0.15"
            />
          </svg>

          {/* city labels */}
          <div className="absolute top-[18px] left-[22px] text-[11px] font-bold tracking-wide text-[#5a77a8] uppercase">Ahmedabad</div>
          <div className="absolute top-[18px] right-[110px] text-[11px] font-bold tracking-wide text-[#5a77a8] uppercase">Gandhinagar</div>
          <div className="absolute top-[44px] left-[24px] text-[8px] text-[#3a567e]">S.G. Highway</div>
          <div className="absolute top-[102px] left-[24px] text-[8px] text-[#3a567e]">Shahibaug Rd</div>
          <div className="absolute top-[164px] left-[24px] text-[8px] text-[#3a567e]">Naranpura Rd</div>
          <div className="absolute top-[212px] left-[360px] text-[8px] text-[#3a567e]">Gift City Rd</div>

          {/* camera markers - green */}
          {[
            { x: 90, y: 110, id: "C-001" },
            { x: 230, y: 145, id: "C-007" },
            { x: 380, y: 200, id: "C-015" },
            { x: 150, y: 85, id: "C-112" },
            { x: 310, y: 100, id: "C-089" },
            { x: 520, y: 120, id: "C-042" },
            { x: 180, y: 210, id: "C-115" },
            { x: 420, y: 260, id: "C-207" },
          ].map((m) => (
            <div
              key={m.id}
              className="absolute -translate-x-1/2 -translate-y-1/2 group cursor-pointer"
              style={{ left: m.x, top: m.y }}
            >
              <div className="w-[14px] h-[14px] rounded-full bg-[#0f2818] border border-[#22c55e] flex items-center justify-center shadow-[0_0_8px_rgba(34,197,94,0.5)]">
                <div className="w-[5px] h-[5px] rounded-full bg-[#22c55e]" />
              </div>
              <div className="absolute left-1/2 -translate-x-1/2 top-[16px] text-[7px] font-bold px-[3px] py-[1px] rounded bg-black/70 text-[#86efac] border border-[#22c55e]/30 whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity">
                {m.id}
              </div>
            </div>
          ))}

          {/* orange warning marker */}
          <div className="absolute left-[320px] top-[175px] -translate-x-1/2 -translate-y-1/2">
            <div className="w-[16px] h-[16px] rounded-full bg-[#2a1f0f] border border-[#f97316] flex items-center justify-center shadow-[0_0_10px_rgba(249,115,22,0.6)] animate-pulse">
              <div className="w-[6px] h-[6px] rounded-full bg-[#f97316]" />
            </div>
          </div>

          {/* red critical marker - C-038 */}
          <div className="absolute left-[480px] top-[235px] -translate-x-1/2 -translate-y-1/2 z-20">
            <div className="relative">
              <div className="w-[22px] h-[22px] rounded-full bg-[#3a1010] border-2 border-[#ef4444] flex items-center justify-center shadow-[0_0_14px_rgba(239,68,68,0.8)] animate-pulse">
                <div className="w-[8px] h-[8px] rounded-full bg-[#ef4444]" />
              </div>
              <div className="absolute -top-[2px] -right-[2px] w-[8px] h-[8px] bg-[#ef4444] rounded-full border border-black animate-ping" />
            </div>
          </div>

          {/* numbered route points 1-4 */}
          {[
            { n: 1, x: 90, y: 110 },
            { n: 2, x: 230, y: 145 },
            { n: 3, x: 380, y: 200 },
            { n: 4, x: 480, y: 235, alert: true },
          ].map((p) => (
            <div
              key={p.n}
              className="absolute -translate-x-1/2 -translate-y-1/2 z-10"
              style={{ left: p.x, top: p.y - 22 }}
            >
              <div
                className={`w-[18px] h-[18px] rounded-full border flex items-center justify-center text-[10px] font-bold shadow-lg ${
                  p.alert
                    ? "bg-[#dc2626] border-white text-white shadow-[0_0_10px_rgba(220,38,38,0.6)]"
                    : "bg-[#1d4ed8] border-white/80 text-white shadow-[0_0_8px_rgba(59,130,246,0.5)]"
                }`}
              >
                {p.n}
              </div>
            </div>
          ))}

          {/* water / river shape */}
          <div className="absolute bottom-[20px] left-[0px] right-[0px] h-[40px] bg-gradient-to-r from-[#0a1a3a]/40 to-[#0f2a5a]/30 blur-[0.5px] border-t border-[#1a3a6a]/20" />
        </div>

        {/* map controls */}
        <div className="absolute top-[12px] right-[12px] flex flex-col gap-[6px]">
          <button className="w-[28px] h-[28px] rounded-[6px] bg-[#0e1a33]/90 backdrop-blur border border-[#1e355e] flex items-center justify-center hover:bg-[#162a52]">
            <ZoomIn className="w-[14px] h-[14px] text-[#8aa0c8]" />
          </button>
          <button className="w-[28px] h-[28px] rounded-[6px] bg-[#0e1a33]/90 backdrop-blur border border-[#1e355e] flex items-center justify-center hover:bg-[#162a52]">
            <ZoomOut className="w-[14px] h-[14px] text-[#8aa0c8]" />
          </button>
          <button className="w-[28px] h-[28px] rounded-[6px] bg-[#0e1a33]/90 backdrop-blur border border-[#1e355e] flex items-center justify-center hover:bg-[#162a52]">
            <Crosshair className="w-[14px] h-[14px] text-[#8aa0c8]" />
          </button>
        </div>

        {/* scale */}
        <div className="absolute bottom-[10px] left-[12px] flex items-center gap-[6px] bg-[#0a1123]/80 backdrop-blur px-[8px] h-[20px] rounded-[4px] border border-[#1a2c52]">
          <div className="w-[40px] h-[2px] bg-[#3a567e] relative">
            <div className="absolute left-0 top-[-3px] w-[1px] h-[8px] bg-[#3a567e]" />
            <div className="absolute right-0 top-[-3px] w-[1px] h-[8px] bg-[#3a567e]" />
          </div>
          <span className="text-[8px] text-[#5a77a8]">2 km</span>
        </div>

        {/* red floating alert popup */}
        <div className="absolute right-[18px] top-[58px] w-[220px] rounded-[8px] bg-[#1a0f0f] border border-[#7f1d1d] shadow-[0_8px_30px_rgba(0,0,0,0.6),0_0_20px_rgba(239,68,68,0.25)] overflow-hidden z-30">
          <div className="bg-gradient-to-r from-[#7f1d1d] to-[#991b1b] px-[10px] h-[28px] flex items-center gap-[6px]">
            <AlertTriangle className="w-[12px] h-[12px] text-white" />
            <span className="text-[10px] font-bold tracking-wide text-white uppercase">Alert: Watchlist Match</span>
            <div className="ml-auto w-[6px] h-[6px] rounded-full bg-white animate-pulse" />
          </div>
          <div className="p-[10px] space-y-[4px]">
            <div className="flex justify-between text-[10px]">
              <span className="text-[#8aa0c8]">Vehicle</span>
              <span className="font-bold text-white tracking-wide">GJ01AB1234</span>
            </div>
            <div className="flex justify-between text-[10px]">
              <span className="text-[#8aa0c8]">Camera</span>
              <span className="font-semibold text-[#fca5a5]">C-038</span>
            </div>
            <div className="flex justify-between text-[10px]">
              <span className="text-[#8aa0c8]">Location</span>
              <span className="text-[#cbd5e1]">Gift City Road</span>
            </div>
            <div className="flex justify-between text-[10px]">
              <span className="text-[#8aa0c8]">Time</span>
              <span className="text-[#cbd5e1] font-mono">10:44:03 AM</span>
            </div>
            <button className="w-full mt-[8px] h-[26px] rounded-[6px] bg-[#dc2626] hover:bg-[#b91c1c] text-white text-[10px] font-bold tracking-wide transition-colors">
              View Details
            </button>
          </div>
        </div>

        {/* live indicator */}
        <div className="absolute bottom-[10px] right-[12px] flex items-center gap-[6px] bg-[#0a1123]/80 backdrop-blur px-[8px] h-[22px] rounded-full border border-[#1a2c52]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#22c55e] animate-pulse shadow-[0_0_6px_#22c55e]" />
          <span className="text-[9px] font-semibold text-[#86efac] tracking-wide">LIVE TRACKING</span>
        </div>
      </div>
    </div>
  );
}
