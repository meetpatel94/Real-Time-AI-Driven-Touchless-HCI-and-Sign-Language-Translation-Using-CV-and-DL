import { Search } from "lucide-react";

export default function VehicleSearch() {
  return (
    <div className="panel rounded-[10px] overflow-hidden flex flex-col h-full">
      <div className="panel-header h-[36px] flex items-center justify-between px-[12px] shrink-0">
        <div className="flex items-center gap-[8px]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#3b82f6] shadow-[0_0_6px_#3b82f6]" />
          <span className="text-[10px] font-bold tracking-[0.14em] uppercase text-white">Vehicle Search</span>
        </div>
      </div>

      <div className="p-[10px] space-y-[10px] flex-1 flex flex-col">
        {/* Search input */}
        <div className="flex gap-[6px]">
          <div className="relative flex-1">
            <input
              defaultValue="GJ01AB1234"
              className="w-full h-[32px] bg-[#0a1123] border border-[#1e355e] rounded-[6px] pl-[10px] pr-[10px] text-[12px] font-mono font-bold text-white tracking-wide focus:outline-none focus:border-[#3b82f6]"
            />
          </div>
          <button className="h-[32px] px-[14px] rounded-[6px] bg-[#1d4ed8] hover:bg-[#1e40af] border border-[#3b82f6]/50 text-white text-[11px] font-bold tracking-wide flex items-center gap-[6px] transition-colors shadow-[0_0_12px_rgba(59,130,246,0.3)]">
            <Search className="w-[12px] h-[12px]" />
            Search
          </button>
        </div>

        {/* Vehicle snapshot */}
        <div className="relative rounded-[8px] overflow-hidden border border-[#1a2c52] bg-[#080c1a] h-[92px] shrink-0">
          <img
            src="https://images.unsplash.com/photo-1552519507-da3b142c6e3d?w=400&h=200&fit=crop&crop=center"
            alt="Vehicle"
            className="w-full h-full object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black/70 to-transparent" />
          <div className="absolute bottom-[6px] left-[8px] flex items-center gap-[4px]">
            <div className="w-[4px] h-[4px] rounded-full bg-[#22c55e] shadow-[0_0_6px_#22c55e]" />
            <span className="text-[9px] text-white font-medium">Live Capture • C-038</span>
          </div>
          <div className="absolute top-[6px] right-[6px] bg-black/60 backdrop-blur px-[6px] h-[18px] rounded-[4px] border border-white/10 flex items-center">
            <span className="text-[9px] font-bold text-white">HD</span>
          </div>
        </div>

        {/* Plate and badge */}
        <div className="space-y-[8px]">
          <div className="flex items-center justify-between">
            <div className="text-[18px] font-black tracking-[0.08em] font-mono text-white">GJ01AB1234</div>
            <div className="w-[20px] h-[20px] rounded-full bg-[#1a2c52] border border-[#2a4a8a] flex items-center justify-center">
              <span className="text-[10px]">🇮🇳</span>
            </div>
          </div>
          <div className="inline-flex items-center gap-[6px] px-[8px] h-[22px] rounded-[6px] bg-[#2a1313] border border-[#7f1d1d] text-[#fca5a5] text-[9px] font-bold tracking-[0.08em] uppercase">
            <div className="w-[4px] h-[4px] rounded-full bg-[#ef4444] animate-pulse" />
            Watchlist Match
          </div>

          <div className="space-y-[6px] pt-[6px] border-t border-[#1a2c52]/60">
            <div className="flex justify-between text-[10px]">
              <span className="text-[#5a77a8]">Vehicle Type</span>
              <span className="text-[#cbd5e1] font-medium">White Swift Dzire</span>
            </div>
            <div className="flex justify-between text-[10px]">
              <span className="text-[#5a77a8]">Color</span>
              <div className="flex items-center gap-[6px]">
                <div className="w-[10px] h-[10px] rounded-full bg-white border border-[#2a4a8a]" />
                <span className="text-[#cbd5e1] font-medium">White</span>
              </div>
            </div>
            <div className="flex justify-between text-[10px]">
              <span className="text-[#5a77a8]">First Seen</span>
              <span className="text-[#cbd5e1] font-mono">10:21:15 AM</span>
            </div>
            <div className="flex justify-between text-[10px]">
              <span className="text-[#5a77a8]">Last Seen</span>
              <span className="text-[#fca5a5] font-mono font-bold">10:44:03 AM</span>
            </div>
          </div>
        </div>

        <div className="mt-auto pt-[8px]">
          <button className="w-full h-[28px] rounded-[6px] bg-[#0e1a33] border border-[#1e355e] text-[#7aa2d6] hover:text-white hover:bg-[#122040] text-[10px] font-semibold tracking-wide transition-colors">
            View Full History →
          </button>
        </div>
      </div>
    </div>
  );
}
