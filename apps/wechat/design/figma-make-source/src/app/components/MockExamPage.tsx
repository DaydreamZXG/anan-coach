import React, { useState } from "react";
import { 
  ChevronLeft, 
  MoreHorizontal, 
  FileText, 
  Grid, 
  Clock, 
  Pause, 
  Edit3, 
  ChevronRight, 
  ChevronDown, 
  Maximize2,
  Flag,
  Check
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";

// Mock Data
const modules = [
  { name: "政治理论", count: 20, start: 1 },
  { name: "常识判断", count: 20, start: 21 },
  { name: "言语理解", count: 40, start: 41 },
  { name: "数量关系", count: 15, start: 81 },
  { name: "判断推理", count: 35, start: 96 },
  { name: "资料分析", count: 20, start: 131 },
];

export const MockExamPage = ({ onExit }: { onExit?: () => void }) => {
  const [currentQ, setCurrentQ] = useState(132); // 资料分析
  const [sheetOpen, setSheetOpen] = useState(false);
  const [materialOpen, setMaterialOpen] = useState(false);
  const [answers, setAnswers] = useState<Record<number, string>>({ 131: "A" });
  const [marked, setMarked] = useState<Record<number, boolean>>({ 132: true });

  const currentModule = "资料分析";
  const totalQ = 150;

  // Options
  const options = [
    { id: "A", text: "21.3%" },
    { id: "B", text: "23.5%" },
    { id: "C", text: "25.8%" },
    { id: "D", text: "28.1%" }
  ];

  return (
    <div className="flex flex-col md:flex-row h-screen w-full bg-[#F7F9FC] text-gray-900 overflow-hidden font-sans">
      
      {/* ---------------- MOBILE / TABLET MATERIAL (Left Panel on Tablet) ---------------- */}
      <div className={`flex-col md:w-[44%] md:border-r md:border-gray-200 md:bg-white md:flex ${materialOpen ? "flex fixed inset-0 z-40 bg-white md:relative md:z-0" : "hidden md:flex"}`}>
        {/* Tablet Top Bar (Material Side) */}
        <div className="hidden md:flex h-14 items-center justify-between px-6 border-b border-gray-100 shrink-0">
           <div className="flex items-center gap-3">
             <button onClick={onExit} className="p-2 -ml-2 rounded-full hover:bg-gray-50 text-gray-500">
               <ChevronLeft size={20} />
             </button>
             <span className="font-bold text-[14px] text-gray-900">2024年国家公务员考试（行政职业能力测验）</span>
           </div>
        </div>

        {/* Material Header */}
        <div className="h-12 bg-indigo-50/50 border-b border-indigo-100/50 flex items-center justify-between px-4 md:px-6 shrink-0 md:bg-white md:border-gray-100">
          <div className="flex items-center gap-2">
            <FileText size={16} className="text-indigo-600 md:text-gray-400" />
            <span className="text-[13px] font-bold text-indigo-900 md:text-gray-900">材料二 · 共5题</span>
            <span className="text-[11px] font-medium text-indigo-500/70 bg-indigo-100/50 px-2 py-0.5 rounded md:bg-gray-100 md:text-gray-500">上次读到第3段</span>
          </div>
          <div className="flex items-center gap-3">
            <button className="text-gray-400 p-1 hover:text-gray-600"><Maximize2 size={16} /></button>
            <button onClick={() => setMaterialOpen(false)} className="md:hidden text-gray-400 p-1"><ChevronDown size={20} /></button>
          </div>
        </div>
        
        {/* Material Content */}
        <div className="flex-1 overflow-y-auto px-5 py-6 md:px-8 md:py-8 text-[15px] leading-[1.8] text-gray-800">
          <p className="mb-4 indent-8">
            2022年，全国居民人均可支配收入36883元，比上年名义增长5.0%，扣除价格因素，实际增长2.9%。分城乡看，城镇居民人均可支配收入49283元，增长（以下如无特别说明，均为同比名义增长）3.9%，扣除价格因素，实际增长1.9%；农村居民人均可支配收入20133元，增长6.3%，扣除价格因素，实际增长4.2%。
          </p>
          <p className="mb-4 indent-8">
            按收入来源分，2022年，全国居民人均工资性收入20590元，增长4.9%，占可支配收入的比重为55.8%；人均经营净收入6175元，增长4.8%，占可支配收入的比重为16.7%；人均财产净收入3227元，增长4.9%，占可支配收入的比重为8.7%；人均转移净收入6892元，增长5.5%，占可支配收入的比重为18.7%。
          </p>
          {/* Mock Table */}
          <div className="w-full border border-gray-200 rounded-lg overflow-hidden my-6">
            <div className="bg-gray-50 px-4 py-2 border-b border-gray-200 text-[12px] font-bold text-gray-600 text-center">
              2022年全国居民人均收支情况
            </div>
            <div className="p-4 flex items-center justify-center text-gray-400 text-[13px]">
              [ 复杂表格数据 ]
            </div>
          </div>
        </div>
      </div>

      {/* ---------------- MOBILE / TABLET QUESTION (Right Panel on Tablet) ---------------- */}
      <div className="flex-1 flex flex-col h-full relative z-10 md:w-[56%] bg-white md:bg-[#F7F9FC]">
        
        {/* Top Bar (Mobile: Main Bar, Tablet: Right Panel Bar) */}
        <div className="h-[52px] bg-white border-b border-gray-100 flex items-center justify-between px-4 shrink-0 shadow-[0_2px_10px_rgba(0,0,0,0.02)] md:shadow-none md:bg-transparent md:px-6">
           {/* Left */}
           <div className="flex items-center gap-3">
             <button onClick={onExit} className="md:hidden p-1.5 -ml-1.5 rounded-full hover:bg-gray-50">
               <ChevronLeft size={22} className="text-gray-900" />
             </button>
             <div className="flex items-baseline gap-1">
               <span className="text-[18px] font-bold text-gray-900">{currentQ}</span>
               <span className="text-[12px] font-medium text-gray-400">/ {totalQ}</span>
             </div>
             <span className="text-[11px] font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded ml-1">{currentModule}</span>
           </div>

           {/* Right Tools */}
           <div className="flex items-center gap-4">
             <div className="flex items-center gap-1.5 text-gray-900 bg-gray-50 px-3 py-1.5 rounded-full font-mono text-[14px] font-bold">
               <Clock size={14} className="text-gray-400" />
               01:12:36
             </div>
             <button className="text-gray-600 hover:text-gray-900"><Pause size={18} /></button>
             <button className="text-gray-600 hover:text-gray-900"><Edit3 size={18} /></button>
             <button onClick={() => setSheetOpen(true)} className="text-gray-600 hover:text-gray-900"><Grid size={18} /></button>
           </div>
        </div>

        {/* Mobile Material Trigger */}
        <button 
          onClick={() => setMaterialOpen(true)}
          className="md:hidden h-10 bg-indigo-50/60 border-b border-indigo-100/50 flex items-center justify-between px-4 shrink-0 active:bg-indigo-100/50 transition-colors"
        >
          <div className="flex items-center gap-2">
            <FileText size={14} className="text-indigo-500" />
            <span className="text-[12px] font-bold text-indigo-900">材料二 · 共5题</span>
            <span className="text-[10px] font-medium text-indigo-500/70 ml-1">上次读到第3段</span>
          </div>
          <ChevronDown size={16} className="text-indigo-400" />
        </button>

        {/* Question Area */}
        <div className="flex-1 overflow-y-auto px-5 py-6 md:px-12 md:py-10">
          <div className="flex gap-2 items-start mb-8">
            <span className="text-[16px] font-bold text-gray-900 mt-1">{currentQ}.</span>
            <p className="text-[16px] font-bold text-gray-900 leading-[1.6]">
              2022年，全国城镇居民人均可支配收入占全国居民人均可支配收入的比重约为：
            </p>
          </div>

          <div className="flex flex-col gap-3">
            {options.map((opt) => {
              const isSelected = answers[currentQ] === opt.id;
              return (
                <button
                  key={opt.id}
                  onClick={() => setAnswers({...answers, [currentQ]: opt.id})}
                  className={`w-full flex items-center p-4 rounded-[16px] border-[1.5px] transition-all text-left ${
                    isSelected 
                      ? "border-cyan-500 bg-cyan-50/30 shadow-[0_2px_10px_rgba(6,182,212,0.1)]" 
                      : "border-gray-100 bg-white hover:border-gray-200"
                  }`}
                >
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-[14px] shrink-0 mr-4 transition-colors ${
                    isSelected ? "bg-cyan-500 text-white" : "bg-gray-100 text-gray-500"
                  }`}>
                    {opt.id}
                  </div>
                  <span className={`text-[15px] font-bold ${isSelected ? "text-cyan-900" : "text-gray-700"}`}>
                    {opt.text}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Bottom Action Bar */}
        <div className="h-16 bg-white border-t border-gray-100 flex items-center justify-between px-4 md:px-8 shrink-0 pb-safe">
          <button className="flex items-center gap-1.5 text-[14px] font-bold text-gray-400 active:scale-95 transition-transform">
            <ChevronLeft size={18} /> 上一题
          </button>
          
          <div className="flex items-center gap-6">
            <button 
              onClick={() => setMarked({...marked, [currentQ]: !marked[currentQ]})}
              className={`flex items-center gap-1.5 text-[14px] font-bold active:scale-95 transition-transform ${marked[currentQ] ? "text-orange-500" : "text-gray-400"}`}
            >
              <Flag size={16} className={marked[currentQ] ? "fill-orange-500" : ""} /> 
              {marked[currentQ] ? "已标记" : "标记"}
            </button>
            <button className="text-[14px] font-bold text-gray-400 active:scale-95 transition-transform">
              暂不作答
            </button>
          </div>

          <button className="flex items-center gap-1.5 text-[14px] font-bold text-gray-900 active:scale-95 transition-transform">
            下一题 <ChevronRight size={18} />
          </button>
        </div>
      </div>

      {/* ---------------- ANSWER SHEET DRAWER ---------------- */}
      <AnimatePresence>
        {sheetOpen && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-gray-900/30 backdrop-blur-sm flex flex-col justify-end md:justify-center md:items-center"
            onClick={() => setSheetOpen(false)}
          >
            <motion.div 
              initial={{ y: "100%" }}
              animate={{ y: 0 }}
              exit={{ y: "100%" }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="bg-[#F7F9FC] rounded-t-[32px] md:rounded-[32px] w-full md:w-[600px] h-[85vh] md:h-[80vh] flex flex-col overflow-hidden shadow-2xl"
              onClick={e => e.stopPropagation()}
            >
              {/* Header */}
              <div className="bg-white p-5 pb-4 border-b border-gray-100 flex justify-between items-center shrink-0">
                 <div>
                   <h3 className="text-[18px] font-bold text-gray-900 tracking-tight mb-1">答题卡</h3>
                   <div className="flex gap-4 text-[12px] font-medium text-gray-500">
                     <span className="flex items-center gap-1"><div className="w-2.5 h-2.5 rounded bg-cyan-100 border border-cyan-200" /> 已答</span>
                     <span className="flex items-center gap-1"><div className="w-2.5 h-2.5 rounded bg-white border border-gray-200" /> 未答</span>
                     <span className="flex items-center gap-1"><div className="w-2.5 h-2.5 rounded bg-orange-50 border border-orange-200" /> 标记</span>
                   </div>
                 </div>
                 <button onClick={() => setSheetOpen(false)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center text-gray-500">
                   <ChevronDown size={18} />
                 </button>
              </div>

              {/* Grid */}
              <div className="flex-1 overflow-y-auto p-5 pb-10 space-y-8">
                {modules.map((mod, mIdx) => (
                  <div key={mIdx}>
                    <h4 className="text-[14px] font-bold text-gray-900 mb-3 ml-1">{mod.name} <span className="text-gray-400 font-medium text-[12px] ml-1">{mod.count}题</span></h4>
                    <div className="grid grid-cols-5 md:grid-cols-8 gap-2.5">
                      {Array.from({ length: mod.count }).map((_, i) => {
                        const qNum = mod.start + i;
                        const isAns = answers[qNum] !== undefined;
                        const isMark = marked[qNum];
                        const isCur = currentQ === qNum;

                        let style = "bg-white border-gray-200 text-gray-600"; // default unans
                        if (isAns) style = "bg-cyan-50 border-cyan-200 text-cyan-700 font-bold";
                        if (isMark) style = "bg-orange-50 border-orange-200 text-orange-600 font-bold";
                        if (isCur) style = "bg-gray-900 border-gray-900 text-white font-bold shadow-md";

                        return (
                          <button 
                            key={qNum}
                            onClick={() => { setCurrentQ(qNum); setSheetOpen(false); }}
                            className={`h-10 rounded-[10px] border flex items-center justify-center text-[14px] active:scale-95 transition-all ${style}`}
                          >
                            {qNum}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>

              {/* Footer */}
              <div className="bg-white p-5 border-t border-gray-100 flex items-center justify-between shrink-0 pb-safe">
                <div>
                  <p className="text-[13px] font-bold text-gray-900">已答 <span className="text-cyan-600">1</span> / 130</p>
                  <p className="text-[11px] font-medium text-gray-500 mt-0.5">有 1 道标记题</p>
                </div>
                <button className="px-6 py-3.5 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] shadow-[0_4px_15px_rgba(0,0,0,0.1)] active:scale-[0.98] transition-transform">
                  检查后交卷
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
