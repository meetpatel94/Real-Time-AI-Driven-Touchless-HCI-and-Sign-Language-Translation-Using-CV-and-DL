import { Maximize2, MoreVertical, Video } from "lucide-react";

const feeds = [
  {
    id: "C-001",
    loc: "Shahibaug Road, Ahmedabad",
    img: "https://images.unsplash.com/photo-1449824913935-59a10b8d2000?w=600&h=340&fit=crop&crop=center",
    status: "Recording",
  },
  {
    id: "C-038",
    loc: "Gift City Road, Gandhinagar",
    img: "https://images.unsplash.com/photo-1494783367193-149034c05e8f?w=600&h=340&fit=crop&crop=center",
    status: "AI Tracking",
    alert: true,
  },
  {
    id: "C-115",
    loc: "S.G. Highway, Ahmedabad",
    img: "https://images.unsplash.com/photo-1465447142348-e9952c393450?w=600&h=340&fit=crop&crop=center",
    status: "Recording",
  },
  {
    id: "C-207",
    loc: "Vadodara City Center",
    img: "https://images.unsplash.com/photo-1519003722824-19424363323d?w=600&h=340&fit=crop&crop=center",
    status: "Recording",
  },
];

export default function LiveFeeds() {
  return (
    <div className="panel rounded-[10px] overflow-hidden flex flex-col h-full">
      <div className="panel-header h-[36px] flex items-center justify-between px-[12px] shrink-0">
        <div className="flex items-center gap-[8px]">
          <div className="w-[6px] h-[6px] rounded-full bg-[#22c55e] animate-pulse shadow-[0_0_6px_#22c55e]" />
          <span className="text-[10px] font-bold tracking-[0.14em] uppercase text-white">Live CCTV Feeds</span>
          <span className="text-[9px] px-[5px] py-[1px] rounded bg-[#122a52] border border-[#1e3f7a] text-[#60a5fa] font-semibold">4 ACTIVE</span>
        </div>
        <div className="flex items-center gap-[8px]">
          <button className="text-[10px] font-semibold text-[#7aa2d6] hover:text-white transition-colors">View All</button>
          <MoreVertical className="w-[14px] h-[14px] text-[#4a5f86]" />
        </div>
      </div>

      <div className="p-[8px] grid grid-cols-2 gap-[8px] flex-1">
        {feeds.map((f) => (
          <div key={f.id} className="relative rounded-[8px] overflow-hidden bg-[#080c1a] border border-[#1a2c52] group">
            <img src={f.img} alt={f.loc} className="w-full h-[132px] object-cover" />
            {/* gradient overlays */}
            <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/10 to-black/20" />
            {/* top bar */}
            <div className="absolute top-[6px] left-[6px] right-[6px] flex items-center justify-between">
              <div className="flex items-center gap-[6px]">
                <div className="flex items-center gap-[4px] bg-black/60 backdrop-blur px-[6px] h-[18px] rounded-[4px] border border-white/10">
                  <Video className="w-[10px] h-[10px] text-white" />
                  <span className="text-[9px] font-bold tracking-wide text-white">{f.id}</span>
                </div>
                <div className="flex items-center gap-[4px] bg-[#16a34a] px-[5px] h-[18px] rounded-[4px] shadow-[0_0_8px_rgba(34,197,94,0.5)]">
                  <div className="w-[4px] h-[4px] rounded-full bg-white animate-pulse" />
                  <span className="text-[8px] font-black tracking-widest text-white">LIVE</span>
                </div>
              </div>
              <div className="w-[20px] h-[20px] rounded-[4px] bg-black/50 backdrop-blur border border-white/10 flex items-center justify-center">
                <Maximize2 className="w-[10px] h-[10px] text-white/80" />
              </div>
            </div>

            {/* bottom bar */}
            <div className="absolute bottom-0 left-0 right-0 p-[6px] flex items-center justify-between">
              <div>
                <div className="text-[10px] font-semibold text-white leading-[1.1] drop-shadow">{f.loc}</div>
                <div className="flex items-center gap-[4px] mt-[2px]">
                  <div className={`w-[4px] h-[4px] rounded-full ${f.alert ? "bg-[#ef4444]" : "bg-[#22c55e]"}`} />
                  <span className={`text-[8.5px] font-medium ${f.alert ? "text-[#fca5a5]" : "text-[#86efac]"}`}>{f.status}</span>
                  {f.alert && <span className="text-[8px] px-[4px] py-[0px] rounded bg-[#dc2626] text-white font-bold ml-[4px]">ALERT</span>}
                </div>
              </div>
              <div className="flex items-center gap-[3px]">
                <div className="w-[18px] h-[4px] rounded-full bg-white/20 overflow-hidden">
                  <div className="h-full w-[60%] bg-[#22c55e]" />
                </div>
                <span className="text-[8px] text-white/60">HD</span>
              </div>
            </div>

            {/* red border if alert */}
            {f.alert && <div className="absolute inset-0 border-2 border-[#ef4444]/70 rounded-[8px] pointer-events-none shadow-[inset_0_0_20px_rgba(239,68,68,0.2)]" />}
          </div>
        ))}
      </div>

      <div className="h-[28px] border-t border-[#1a2c52] flex items-center justify-between px-[10px] bg-[#0c142a]">
        <div className="flex items-center gap-[6px]">
          <div className="w-[28px] h-[3px] rounded-full bg-[#1e3a6a]" />
          <span className="text-[9px] text-[#5a77a8]">4 of 12,842 cameras</span>
        </div>
        <div className="flex items-center gap-[2px]">
          <div className="w-[12px] h-[3px] rounded-full bg-[#3b82f6]" />
          <div className="w-[6px] h-[3px] rounded-full bg-[#1e3a6a]" />
          <div className="w-[6px] h-[3px] rounded-full bg-[#1e3a6a]" />
        </div>
      </div>
    </div>
  );
}
