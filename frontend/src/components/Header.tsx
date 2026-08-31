import { Bell, Search, Settings } from "lucide-react";

export default function Header() {
  return (
    <header className="h-[52px] bg-[#0a1123] border-b border-[#1a2c52] flex items-center px-[14px] gap-[14px] shrink-0">
      {/* Search */}
      <div className="flex-1 flex justify-center">
        <div className="relative w-[560px] max-w-full">
          <Search className="absolute left-[12px] top-1/2 -translate-y-1/2 w-[14px] h-[14px] text-[#5a77a8]" />
          <input
            placeholder="Search Vehicle / Camera / Location..."
            className="w-full h-[34px] bg-[#0e1a33] border border-[#1e355e] rounded-[8px] pl-[34px] pr-[12px] text-[12.5px] text-[#cbd6ea] placeholder:text-[#5a6f96] focus:outline-none focus:border-[#2a4a8a] focus:bg-[#101d3a] transition-all"
          />
          <div className="absolute right-[8px] top-1/2 -translate-y-1/2 hidden md:flex items-center gap-[4px]">
            <span className="text-[10px] px-[5px] py-[2px] rounded bg-[#162a52] border border-[#1e3a6a] text-[#5a77a8]">⌘</span>
            <span className="text-[10px] px-[5px] py-[2px] rounded bg-[#162a52] border border-[#1e3a6a] text-[#5a77a8]">K</span>
          </div>
        </div>
      </div>

      {/* Right */}
      <div className="flex items-center gap-[12px] shrink-0">
        <button className="relative w-[34px] h-[34px] rounded-[8px] bg-[#0e1a33] border border-[#1e355e] flex items-center justify-center hover:bg-[#122040] transition-colors">
          <Bell className="w-[16px] h-[16px] text-[#8aa0c8]" />
          <span className="absolute -top-[4px] -right-[4px] bg-[#dc2626] text-white text-[10px] font-bold min-w-[18px] h-[18px] px-[4px] flex items-center justify-center rounded-full border border-[#0a1123]">12</span>
        </button>
        <button className="w-[34px] h-[34px] rounded-[8px] bg-[#0e1a33] border border-[#1e355e] flex items-center justify-center hover:bg-[#122040] transition-colors">
          <Settings className="w-[16px] h-[16px] text-[#8aa0c8]" />
        </button>

        <div className="h-[24px] w-px bg-[#1a2c52] mx-[2px]" />

        <div className="flex items-center gap-[10px] pl-[2px]">
          <div className="w-[34px] h-[34px] rounded-full bg-gradient-to-br from-[#3b82f6] to-[#1d4ed8] p-[1.5px] shadow-[0_0_10px_rgba(59,130,246,0.35)]">
            <div className="w-full h-full rounded-full bg-[#0e1a33] flex items-center justify-center overflow-hidden">
              <img
                src="https://i.pravatar.cc/100?img=15"
                alt="Inspector"
                className="w-full h-full object-cover"
              />
            </div>
          </div>
          <div className="leading-[1.15] hidden lg:block">
            <div className="text-[12px] font-semibold text-white tracking-[0.01em]">Inspector Rajveer</div>
            <div className="text-[10px] text-[#7a94c0]">Gandhinagar Command</div>
          </div>
          <div className="w-[6px] h-[6px] rounded-full bg-[#22c55e] shadow-[0_0_6px_rgba(34,197,94,0.6)] hidden lg:block ml-[4px]" />
        </div>
      </div>
    </header>
  );
}
