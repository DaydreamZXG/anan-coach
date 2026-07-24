import React, { useState } from "react";
import { 
  ChevronLeft, Search, Sparkles, MapPin, 
  Building2, CheckCircle2, AlertTriangle, ChevronRight, 
  BarChart2, Info, ArrowRight, Target, Plus, X, Users, Briefcase, ChevronDown
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { getCaptureState } from "../../capture-state";

type ViewState = "discovery" | "all_positions" | "detail" | "compare" | "impact" | "impact_confirmed";
const viewStates: readonly ViewState[] = ["discovery", "all_positions", "detail", "compare", "impact", "impact_confirmed"];

export const PositionWorkbenchPage = ({ onGo, onBack }: { onGo: (page: string) => void; onBack: () => void }) => {
  const [view, setView] = useState<ViewState>(() => getCaptureState(viewStates, "discovery"));
  const [activePos, setActivePos] = useState<string>("p1");
  const [compareList, setCompareList] = useState<string[]>(() =>
    getCaptureState(viewStates, "discovery") === "compare" ? ["p1", "p2"] : [],
  );
  const [drawer, setDrawer] = useState<"score_calc" | "p3_evidence" | null>(null);

  const posData: Record<string, any> = {
    p1: {
      id: "p1",
      dept: "国家统计局北京调查总队",
      title: "数据管理处 一级主任科员及以下",
      location: "北京西城",
      majorLimit: "严格",
      majorReq: "仅限 计算机科学与技术 (080901)",
      degreeReq: "硕士研究生及以上",
      expReq: "无限制",
      otherReq: "无",
      recruit: 2,
      safeScore: 135.5,
      competitorsEst: "约 120 人",
      aiInsight: "专业限制严格，排除了多数跨专业考生。属于竞争相对明确的优势区间。",
      fitInsight: "该岗位重视数据分析与逻辑，与你当前『资料分析』高分特征契合。",
      tags: ["专业限制严", "进面线平稳"]
    },
    p2: {
      id: "p2",
      dept: "国家税务总局北京市税务局",
      title: "信息中心 一级行政执法员",
      location: "北京海淀",
      majorLimit: "较宽",
      majorReq: "计算机类、电子信息类",
      degreeReq: "本科及以上",
      expReq: "无限制",
      otherReq: "无",
      recruit: 5,
      safeScore: 138.0,
      competitorsEst: "约 850 人",
      aiInsight: "招录人数多但专业大类限制较宽，历年报考热度较高，预计进面分数维持高位。",
      fitInsight: "目标分 138 距离你目前能力（128）差距较大，需在考前完成大幅提分。",
      tags: ["招录量大", "历年高分"]
    },
    p3: {
      id: "p3",
      dept: "天津海关",
      title: "综合业务处 一级行政执法员",
      location: "天津",
      majorLimit: "适中",
      majorReq: "计算机类",
      degreeReq: "本科及以上",
      expReq: "无限制",
      otherReq: "需双眼矫正视力5.0",
      recruit: 3,
      safeScore: 130.5,
      competitorsEst: "约 300 人",
      aiInsight: "如果对工作地点有一定弹性，周边区域岗位可作为备选。预计进面线呈现 3-5 分的相对空间。",
      fitInsight: "进面安全线存在区间缓冲，距你当前能力预估较近，是较为稳妥的备选。",
      tags: ["京津冀辐射", "分数友好"]
    }
  };

  const toggleCompare = (id: string) => {
    if (compareList.includes(id)) {
      setCompareList(compareList.filter(x => x !== id));
    } else {
      if (compareList.length < 2) setCompareList([...compareList, id]);
    }
  };

  // -------------------------------------------------------------------------
  // DISCOVERY VIEW
  // -------------------------------------------------------------------------
  if (view === "discovery") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="absolute top-[48px] right-4 flex items-center z-50 pointer-events-none opacity-50">
          <div className="w-[87px] h-[32px] rounded-full bg-white/50 backdrop-blur-md border border-gray-200/50" />
        </div>

        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px]">
          <button onClick={onBack} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-2 pb-32 no-scrollbar">
          <h1 className="text-[28px] font-bold text-gray-900 tracking-tight mb-6">岗位决策台</h1>
          
          {/* Profile Context */}
          <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-8 relative overflow-hidden">
             <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-50 rounded-full blur-2xl -translate-y-1/2 translate-x-1/3" />
             <div className="relative z-10">
               <p className="text-[12px] font-bold text-gray-400 mb-3">岸岸已载入你的资料档案</p>
               <div className="flex flex-wrap gap-2 mb-4">
                 <span className="px-2.5 py-1 bg-[#F7F9FC] text-gray-700 rounded-md text-[12px] font-bold">计算机科学与技术</span>
                 <span className="px-2.5 py-1 bg-[#F7F9FC] text-gray-700 rounded-md text-[12px] font-bold">硕士</span>
                 <span className="px-2.5 py-1 bg-[#F7F9FC] text-gray-700 rounded-md text-[12px] font-bold">中共党员</span>
                 <span className="px-2.5 py-1 bg-[#F7F9FC] text-gray-700 rounded-md text-[12px] font-bold">无基层经验</span>
               </div>
               <div className="flex items-center justify-between pt-3 border-t border-gray-100">
                 <div className="flex items-center gap-2">
                   <Sparkles size={14} className="text-indigo-500" />
                   <p className="text-[13px] font-medium text-gray-600">共有 <span className="font-bold text-gray-900">142</span> 个国考岗位可报</p>
                 </div>
                 <button onClick={() => setView("all_positions")} className="text-[12px] font-bold text-indigo-500 flex items-center gap-0.5 active:scale-95 bg-indigo-50 px-2 py-1 rounded-md">
                   查看全部 <ChevronRight size={12} />
                 </button>
               </div>
             </div>
          </div>

          <h2 className="text-[18px] font-bold text-gray-900 mb-4 ml-1">专业限制严格的优势岗位 (12)</h2>
          <div className="flex flex-col gap-4 mb-8">
             {[posData.p1, posData.p2].map(pos => (
               <div key={pos.id} onClick={() => { setActivePos(pos.id); setView("detail"); }} className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 active:scale-[0.98] transition-transform cursor-pointer">
                 <p className="text-[12px] text-gray-500 font-bold mb-1">{pos.dept}</p>
                 <h3 className="text-[16px] font-bold text-gray-900 mb-3 leading-snug">{pos.title}</h3>
                 <div className="flex flex-wrap gap-2 mb-4">
                   {pos.tags.map((t: string) => (
                     <span key={t} className="bg-gray-50 text-gray-600 text-[11px] font-bold px-2 py-0.5 rounded">{t}</span>
                   ))}
                 </div>
                 <div className="bg-indigo-50/50 rounded-[16px] p-3 flex gap-2 items-start border border-indigo-50">
                   <Sparkles size={14} className="text-indigo-400 mt-0.5 shrink-0" />
                   <p className="text-[12px] text-gray-600 leading-relaxed font-medium">{pos.aiInsight}</p>
                 </div>
               </div>
             ))}
          </div>

          <h2 className="text-[18px] font-bold text-gray-900 mb-4 ml-1">周边区域的弹性备选方案</h2>
          <div className="flex flex-col gap-4 mb-8">
             <div onClick={() => { setActivePos("p3"); setView("detail"); }} className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 active:scale-[0.98] transition-transform cursor-pointer">
                 <p className="text-[12px] text-gray-500 font-bold mb-1">{posData.p3.dept}</p>
                 <h3 className="text-[16px] font-bold text-gray-900 mb-3 leading-snug">{posData.p3.title}</h3>
                 <div className="flex flex-wrap gap-2 mb-4">
                   {posData.p3.tags.map((t: string) => (
                     <span key={t} className="bg-gray-50 text-gray-600 text-[11px] font-bold px-2 py-0.5 rounded">{t}</span>
                   ))}
                 </div>
                 <div className="bg-cyan-50/50 rounded-[16px] p-3 flex gap-2 items-start border border-cyan-50">
                   <MapPin size={14} className="text-cyan-500 mt-0.5 shrink-0" />
                   <p className="text-[12px] text-gray-600 leading-relaxed font-medium">{posData.p3.aiInsight}</p>
                 </div>
             </div>
          </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // ALL POSITIONS VIEW (List & Filter)
  // -------------------------------------------------------------------------
  if (view === "all_positions") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px] bg-white/80 backdrop-blur-xl border-b border-gray-100">
          <div className="flex items-center gap-2">
            <button onClick={() => setView("discovery")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
            <span className="text-[16px] font-bold text-gray-900">全部可选岗位 (142)</span>
          </div>
        </div>

        <div className="px-5 pt-4 pb-2 bg-white shrink-0 shadow-[0_2px_10px_rgba(0,0,0,0.02)] z-10 border-b border-gray-50">
          <div className="relative mb-3">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input 
              type="text" 
              placeholder="搜索单位、岗位或地点" 
              className="w-full h-10 pl-9 pr-4 bg-[#F7F9FC] border border-gray-100 rounded-full text-[14px] focus:outline-none focus:border-indigo-100 focus:bg-white"
            />
          </div>
          <div className="flex gap-2 overflow-x-auto no-scrollbar">
            {["全国地区", "单位层级", "专业限制", "招录人数"].map(f => (
              <button key={f} className="flex items-center gap-1 bg-[#F7F9FC] border border-gray-100 px-3 py-1.5 rounded-full text-[12px] font-bold text-gray-600 whitespace-nowrap active:scale-95">
                {f} <ChevronDown size={12} />
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-4 pb-32 no-scrollbar">
          <div className="flex flex-col gap-4">
             {Object.values(posData).map(pos => (
               <div key={pos.id + '_all'} onClick={() => { setActivePos(pos.id); setView("detail"); }} className="bg-white rounded-[20px] p-5 shadow-[0_2px_15px_rgba(0,0,0,0.02)] border border-gray-50 active:scale-[0.98] transition-transform cursor-pointer">
                 <div className="flex justify-between items-start mb-1">
                   <p className="text-[12px] text-gray-500 font-bold">{pos.dept}</p>
                   <span className="text-[11px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded">{pos.location}</span>
                 </div>
                 <h3 className="text-[15px] font-bold text-gray-900 mb-3">{pos.title}</h3>
                 <div className="flex items-center justify-between mt-2 pt-3 border-t border-gray-50">
                   <div className="flex items-center gap-3 text-[12px] font-medium text-gray-500">
                     <span>招 {pos.recruit} 人</span>
                     <span className="w-1 h-1 rounded-full bg-gray-300" />
                     <span>限制{pos.majorLimit}</span>
                   </div>
                   <span className="text-[12px] font-bold text-indigo-500">约 {pos.safeScore} 分进面 <ChevronRight size={10} className="inline -mt-0.5" /></span>
                 </div>
               </div>
             ))}
          </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // DETAIL VIEW
  // -------------------------------------------------------------------------
  const pos = posData[activePos];
  if (view === "detail") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="absolute top-[48px] right-4 flex items-center z-50 pointer-events-none opacity-50">
          <div className="w-[87px] h-[32px] rounded-full bg-white/50 backdrop-blur-md border border-gray-200/50" />
        </div>

        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px] bg-[#F7F9FC]/80 backdrop-blur-xl">
          <button onClick={() => setView("discovery")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-2 pb-32 no-scrollbar">
          {/* Header */}
          <div className="mb-6">
            <span className="inline-block bg-gray-200/50 text-gray-600 text-[11px] font-bold px-2 py-1 rounded-md mb-3">{pos.location}</span>
            <h1 className="text-[24px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-2">{pos.title}</h1>
            <p className="text-[15px] font-medium text-gray-500">{pos.dept}</p>
          </div>

          {/* Section 1: 报考门槛 */}
          <h2 className="text-[17px] font-bold text-gray-900 mb-3 ml-1">报考门槛校验</h2>
          <div className="bg-white rounded-[28px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6 flex flex-col gap-4">
             <div className="flex items-start gap-3">
               <CheckCircle2 size={18} className="text-green-500 mt-0.5 shrink-0" />
               <div>
                 <p className="text-[14px] font-bold text-gray-900 mb-0.5">专业要求</p>
                 <p className="text-[12px] font-medium text-gray-500">符合要求：{pos.majorReq}</p>
               </div>
             </div>
             <div className="h-px bg-gray-50 ml-8" />
             <div className="flex items-start gap-3">
               <CheckCircle2 size={18} className="text-green-500 mt-0.5 shrink-0" />
               <div>
                 <p className="text-[14px] font-bold text-gray-900 mb-0.5">学历与政治面貌</p>
                 <p className="text-[12px] font-medium text-gray-500">符合要求：{pos.degreeReq}</p>
               </div>
             </div>
             {pos.otherReq !== "无" && (
               <>
                 <div className="h-px bg-gray-50 ml-8" />
                 <div className="flex items-start gap-3">
                   <AlertTriangle size={18} className="text-orange-500 mt-0.5 shrink-0" />
                   <div>
                     <p className="text-[14px] font-bold text-gray-900 mb-0.5">体检或特殊限制</p>
                     <p className="text-[12px] font-medium text-gray-500">{pos.otherReq}，请自行确认是否受限。</p>
                   </div>
                 </div>
               </>
             )}
          </div>

          {/* Section 2: 竞争与分差 */}
          <h2 className="text-[17px] font-bold text-gray-900 mb-3 ml-1">竞争与分差判断</h2>
          <div className="bg-white rounded-[28px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6 relative overflow-hidden">
             <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-50 rounded-full blur-2xl -translate-y-1/2 translate-x-1/3" />
             <div className="relative z-10">
               <div className="flex items-end gap-3 mb-5">
                 <div>
                   <p className="text-[12px] font-bold text-gray-400 mb-1">建议进面安全线</p>
                   <p className="text-[32px] font-bold text-gray-900 leading-none">{pos.safeScore}</p>
                 </div>
                 <div className="pb-1">
                   <span className="text-[12px] font-bold text-orange-500 bg-orange-50 px-2 py-0.5 rounded-md">距你当前水平差约 {Math.abs(128 - pos.safeScore).toFixed(1)} 分</span>
                 </div>
               </div>

               <div className="flex justify-between items-end h-24 mb-4 mt-6">
                 {/* Mock Chart */}
                 <div className="flex flex-col items-center gap-2 flex-1">
                   <div className="w-8 bg-gray-100 rounded-t-md relative flex items-end justify-center" style={{ height: "60%" }}>
                     <span className="absolute -top-5 text-[10px] font-bold text-gray-400">134.2</span>
                   </div>
                   <span className="text-[10px] font-bold text-gray-400">2021</span>
                 </div>
                 <div className="flex flex-col items-center gap-2 flex-1">
                   <div className="w-8 bg-gray-100 rounded-t-md relative flex items-end justify-center" style={{ height: "70%" }}>
                     <span className="absolute -top-5 text-[10px] font-bold text-gray-400">135.5</span>
                   </div>
                   <span className="text-[10px] font-bold text-gray-400">2022</span>
                 </div>
                 <div className="flex flex-col items-center gap-2 flex-1">
                   <div className="w-8 bg-gray-100 rounded-t-md relative flex items-end justify-center" style={{ height: "55%" }}>
                     <span className="absolute -top-5 text-[10px] font-bold text-gray-400">133.8</span>
                   </div>
                   <span className="text-[10px] font-bold text-gray-400">2023</span>
                 </div>
                 <div className="flex flex-col items-center gap-2 flex-1">
                   <div className="w-8 bg-indigo-500 rounded-t-md relative flex items-end justify-center shadow-md shadow-indigo-500/20" style={{ height: "65%" }}>
                     <span className="absolute -top-5 text-[11px] font-bold text-indigo-600">{pos.safeScore}</span>
                   </div>
                   <span className="text-[10px] font-bold text-indigo-600">今年预估</span>
                 </div>
               </div>

               <div className="flex items-center gap-4 mt-2">
                 <button onClick={() => setDrawer("score_calc")} className="text-[12px] font-bold text-indigo-500 flex items-center gap-1 active:scale-95 transition-transform">
                   预测依据 <ChevronRight size={14} />
                 </button>
                 {pos.id === "p3" && (
                   <button onClick={() => setDrawer("p3_evidence")} className="text-[12px] font-bold text-cyan-600 flex items-center gap-1 active:scale-95 transition-transform">
                     查看区域分差空间 <ChevronRight size={14} />
                   </button>
                 )}
               </div>
             </div>
          </div>

          {/* Section 3: 个人适配 */}
          <h2 className="text-[17px] font-bold text-gray-900 mb-3 ml-1">个人适配程度</h2>
          <div className="bg-indigo-50/50 rounded-[28px] p-5 border border-indigo-50 flex items-start gap-3">
             <Sparkles size={18} className="text-indigo-500 shrink-0 mt-0.5" />
             <p className="text-[14px] text-gray-700 font-medium leading-relaxed">
               {pos.fitInsight}
             </p>
          </div>
        </div>

        {/* Bottom Actions */}
        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
          <div className="flex gap-3">
            <button 
              onClick={() => toggleCompare(activePos)}
              className={`w-[140px] shrink-0 py-4 rounded-[24px] font-bold text-[15px] active:scale-[0.98] transition-all border ${
                compareList.includes(activePos) ? "bg-indigo-50 text-indigo-600 border-indigo-100" : "bg-[#F7F9FC] text-gray-700 border-gray-100"
              }`}
            >
              {compareList.includes(activePos) ? "已加入对比" : `加入对比 (${compareList.length}/2)`}
            </button>
            <button onClick={() => setView("impact")} className="flex-1 py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[15px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
              设为主目标
            </button>
          </div>
          {compareList.length === 2 && (
             <div className="mt-4 flex justify-center">
               <button onClick={() => setView("compare")} className="text-[13px] font-bold text-indigo-500 flex items-center gap-1.5 active:scale-95 transition-transform bg-indigo-50/50 px-3 py-1.5 rounded-full">
                 前往查看横向对比 <ArrowRight size={14} />
               </button>
             </div>
          )}
        </div>

        {/* Drawers */}
        <AnimatePresence>
          {drawer === "score_calc" && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 bg-gray-900/30 backdrop-blur-sm flex flex-col justify-end"
              onClick={() => setDrawer(null)}
            >
              <motion.div 
                initial={{ y: "100%" }}
                animate={{ y: 0 }}
                exit={{ y: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 200 }}
                className="bg-white rounded-t-[32px] p-6 pt-3 pb-safe"
                onClick={e => e.stopPropagation()}
              >
                <div className="w-10 h-1.5 bg-gray-200 rounded-full mx-auto mb-6" />
                <div className="flex justify-between items-center mb-6">
                  <h3 className="text-[20px] font-bold text-gray-900 tracking-tight">分数线预测依据</h3>
                  <button onClick={() => setDrawer(null)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center active:scale-95">
                    <X size={16} className="text-gray-500" />
                  </button>
                </div>
                <div className="bg-[#F7F9FC] rounded-[24px] p-5 border border-gray-100 flex flex-col gap-4 mb-4">
                   <p className="text-[14px] text-gray-600 font-medium leading-relaxed">
                     基于该岗位过去 3 年实际进面平均分（134.5），结合今年统招人数无变动、但『计算机类』大盘报名热度预计上浮的宏观趋势评估。
                   </p>
                   <p className="text-[14px] text-gray-600 font-medium leading-relaxed">
                     为确保容错率，经算法测算，设定此安全进面参考线为 <span className="font-bold text-gray-900">{pos.safeScore}</span> 分。
                   </p>
                </div>
                <button onClick={() => setDrawer(null)} className="w-full py-4 mt-2 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-lg active:scale-[0.98] transition-transform">
                  明白了
                </button>
              </motion.div>
            </motion.div>
          )}

          {drawer === "p3_evidence" && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 bg-gray-900/30 backdrop-blur-sm flex flex-col justify-end"
              onClick={() => setDrawer(null)}
            >
              <motion.div 
                initial={{ y: "100%" }}
                animate={{ y: 0 }}
                exit={{ y: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 200 }}
                className="bg-white rounded-t-[32px] p-6 pt-3 pb-safe"
                onClick={e => e.stopPropagation()}
              >
                <div className="w-10 h-1.5 bg-gray-200 rounded-full mx-auto mb-6" />
                <div className="flex justify-between items-center mb-6">
                  <h3 className="text-[20px] font-bold text-gray-900 tracking-tight">区域分差空间测算</h3>
                  <button onClick={() => setDrawer(null)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center active:scale-95">
                    <X size={16} className="text-gray-500" />
                  </button>
                </div>
                <div className="bg-[#F7F9FC] rounded-[24px] p-5 border border-gray-100 flex flex-col gap-4 mb-4">
                   <p className="text-[14px] text-gray-600 font-medium leading-relaxed">
                     近三年京津冀地区同类岗位进面分抽样对比：
                   </p>
                   <div className="flex flex-col gap-2">
                     <div className="flex justify-between text-[13px] font-medium text-gray-500">
                       <span>年份</span><span>北京核心区</span><span>天津等周边区</span>
                     </div>
                     <div className="h-px bg-gray-200/60" />
                     <div className="flex justify-between text-[13px] font-bold text-gray-800">
                       <span>2023</span><span>135.8</span><span>131.0</span>
                     </div>
                     <div className="flex justify-between text-[13px] font-bold text-gray-800">
                       <span>2022</span><span>137.5</span><span>132.8</span>
                     </div>
                     <div className="flex justify-between text-[13px] font-bold text-gray-800">
                       <span>2021</span><span>136.2</span><span>131.5</span>
                     </div>
                   </div>
                   <p className="text-[14px] text-gray-600 font-medium leading-relaxed mt-2 pt-4 border-t border-gray-100">
                     结论：周边区常年保持约 <span className="font-bold text-gray-900">3.5 - 4.7 分</span> 的稳定分差区间，竞争压力相对缓和。
                   </p>
                </div>
                <button onClick={() => setDrawer(null)} className="w-full py-4 mt-2 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-lg active:scale-[0.98] transition-transform">
                  明白了
                </button>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // COMPARE VIEW
  // -------------------------------------------------------------------------
  if (view === "compare") {
    const c1 = posData[compareList[0]];
    const c2 = posData[compareList[1]];

    // Recommend logic based on multiple factors (mocked deterministically for demo)
    const getRecommended = () => {
      if (c1.id === "p1") return c1.id;
      if (c2.id === "p1") return c2.id;
      return c1.safeScore < c2.safeScore ? c1.id : c2.id;
    };
    const recId = getRecommended();
    const recReason = "综合专业限制严格度、分数变化趋势与当前能力的差距，此岗位竞争风险更可控，更契合目前的备考进度。";

    return (
      <div className="flex flex-col h-full bg-white relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px]">
          <button onClick={() => setView("detail")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
          <span className="text-[16px] font-bold text-gray-900">岗位综合对比</span>
          <div className="w-8" />
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-4 pb-32 no-scrollbar">
           {/* Table Headers */}
           <div className="flex gap-3 mb-6">
              <div className="flex-1">
                 <p className="text-[11px] text-gray-400 font-bold mb-1 truncate">{c1.dept}</p>
                 <h3 className="text-[14px] font-bold text-gray-900 leading-snug h-10">{c1.title}</h3>
              </div>
              <div className="w-px bg-gray-100 shrink-0" />
              <div className="flex-1">
                 <p className="text-[11px] text-gray-400 font-bold mb-1 truncate">{c2.dept}</p>
                 <h3 className="text-[14px] font-bold text-gray-900 leading-snug h-10">{c2.title}</h3>
              </div>
           </div>

           {/* Comparison Rows */}
           <div className="flex flex-col gap-6">
              <div>
                <p className="text-[11px] font-bold text-gray-400 text-center mb-3">安全线与当前差距</p>
                <div className="flex gap-3">
                  <div className="flex-1 bg-[#F7F9FC] rounded-[16px] p-4 text-center border border-gray-50">
                    <p className="text-[20px] font-bold text-gray-900 leading-none mb-1">{c1.safeScore}</p>
                    <p className="text-[11px] font-bold text-orange-500">差 {Math.abs(128 - c1.safeScore).toFixed(1)}分</p>
                  </div>
                  <div className="flex-1 bg-[#F7F9FC] rounded-[16px] p-4 text-center border border-gray-50">
                    <p className="text-[20px] font-bold text-gray-900 leading-none mb-1">{c2.safeScore}</p>
                    <p className="text-[11px] font-bold text-orange-500">差 {Math.abs(128 - c2.safeScore).toFixed(1)}分</p>
                  </div>
                </div>
              </div>

              <div>
                <p className="text-[11px] font-bold text-gray-400 text-center mb-3">专业限制匹配</p>
                <div className="flex gap-3">
                  <div className="flex-1 flex flex-col items-center gap-1">
                    <span className={`text-[13px] font-bold ${c1.majorLimit === "严格" ? "text-green-600" : "text-gray-900"}`}>{c1.majorLimit}</span>
                    <span className="text-[11px] text-gray-500 text-center leading-tight">{c1.majorReq}</span>
                  </div>
                  <div className="flex-1 flex flex-col items-center gap-1">
                    <span className={`text-[13px] font-bold ${c2.majorLimit === "严格" ? "text-green-600" : "text-gray-900"}`}>{c2.majorLimit}</span>
                    <span className="text-[11px] text-gray-500 text-center leading-tight">{c2.majorReq}</span>
                  </div>
                </div>
              </div>

              <div>
                <p className="text-[11px] font-bold text-gray-400 text-center mb-3">预计竞争规模</p>
                <div className="flex gap-3">
                  <div className="flex-1 flex flex-col items-center gap-1">
                    <span className="text-[14px] font-bold text-gray-900">{c1.competitorsEst}</span>
                    <span className="text-[11px] text-gray-500">招 {c1.recruit} 人</span>
                  </div>
                  <div className="flex-1 flex flex-col items-center gap-1">
                    <span className="text-[14px] font-bold text-gray-900">{c2.competitorsEst}</span>
                    <span className="text-[11px] text-gray-500">招 {c2.recruit} 人</span>
                  </div>
                </div>
              </div>

              <div>
                <p className="text-[11px] font-bold text-indigo-400 text-center mb-3 flex justify-center items-center gap-1"><Sparkles size={10}/> 综合推荐建议</p>
                <div className="flex gap-3">
                  <div className={`flex-1 rounded-[16px] p-4 border relative ${recId === c1.id ? "bg-indigo-50/50 border-indigo-100" : "bg-[#F7F9FC] border-gray-50"}`}>
                     {recId === c1.id && <div className="absolute top-0 right-0 -mt-2 -mr-2 bg-indigo-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-sm">综合推荐</div>}
                     <p className={`text-[12px] font-medium leading-relaxed ${recId === c1.id ? "text-gray-800" : "text-gray-500"}`}>
                       {recId === c1.id ? recReason : "竞争不确定性较高，结合能力差距，备考容错率较低。"}
                     </p>
                  </div>
                  <div className={`flex-1 rounded-[16px] p-4 border relative ${recId === c2.id ? "bg-indigo-50/50 border-indigo-100" : "bg-[#F7F9FC] border-gray-50"}`}>
                     {recId === c2.id && <div className="absolute top-0 right-0 -mt-2 -mr-2 bg-indigo-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-sm">综合推荐</div>}
                     <p className={`text-[12px] font-medium leading-relaxed ${recId === c2.id ? "text-gray-800" : "text-gray-500"}`}>
                       {recId === c2.id ? recReason : "竞争不确定性较高，结合能力差距，备考容错率较低。"}
                     </p>
                  </div>
                </div>
              </div>
           </div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40 flex gap-3">
           <button onClick={() => { setActivePos(c1.id); setView("impact"); }} className="flex-1 py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[15px] shadow-[0_4px_15px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
             选定左侧岗位
           </button>
           <button onClick={() => { setActivePos(c2.id); setView("impact"); }} className="flex-1 py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[15px] shadow-[0_4px_15px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
             选定右侧岗位
           </button>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // IMPACT VIEW (Preview before confirmation)
  // -------------------------------------------------------------------------
  if (view === "impact") {
    const finalPos = posData[activePos];
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px]">
          <button onClick={() => setView("detail")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
        </div>

        <div className="flex-1 px-8 flex flex-col justify-center items-center text-center pb-20">
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, ease: "easeOut" }}
            className="flex flex-col items-center w-full"
          >
            <h2 className="text-[26px] font-bold text-gray-900 tracking-tight mb-2">目标变更预览</h2>
            <p className="text-[14px] font-bold text-gray-500 mb-8">{finalPos.dept}</p>

            <div className="w-full bg-white rounded-[32px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex flex-col gap-6 text-left mb-10">
               <h3 className="text-[15px] font-bold text-gray-900 mb-2">备考计划即将发生以下调整：</h3>
               
               <div className="flex items-start gap-4">
                 <div className="w-10 h-10 rounded-full bg-blue-50 flex items-center justify-center shrink-0">
                   <BarChart2 size={18} className="text-blue-500" />
                 </div>
                 <div>
                   <p className="text-[15px] font-bold text-gray-900 mb-1">新目标分：{finalPos.safeScore}</p>
                   <p className="text-[13px] text-gray-500 font-medium leading-relaxed">
                     行测目标上调 2 分，申论目标上调 3 分。
                   </p>
                 </div>
               </div>

               <div className="h-px bg-gray-50 ml-14" />

               <div className="flex items-start gap-4">
                 <div className="w-10 h-10 rounded-full bg-purple-50 flex items-center justify-center shrink-0">
                   <Briefcase size={18} className="text-purple-500" />
                 </div>
                 <div>
                   <p className="text-[15px] font-bold text-gray-900 mb-1">周训练策略变更</p>
                   <p className="text-[13px] text-gray-500 font-medium leading-relaxed">
                     数量关系不再建议战略性放弃，本周起新增 2 小时专项突破。
                   </p>
                 </div>
               </div>
            </div>

            <button onClick={() => setView("impact_confirmed")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform mb-3">
              确认变更，更新我的计划
            </button>
            <button onClick={() => setView("detail")} className="w-full py-4 bg-transparent text-gray-500 rounded-[24px] font-bold text-[15px] active:scale-[0.98] transition-transform">
              返回修改
            </button>
          </motion.div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // IMPACT CONFIRMED VIEW
  // -------------------------------------------------------------------------
  if (view === "impact_confirmed") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        <div className="flex-1 px-8 flex flex-col justify-center items-center text-center pb-20">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="flex flex-col items-center w-full"
          >
            <div className="w-24 h-24 bg-white rounded-full flex items-center justify-center shadow-[0_8px_30px_rgba(0,0,0,0.04)] mb-6 border border-gray-50">
              <CheckCircle2 size={40} className="text-green-500" />
            </div>
            <h2 className="text-[26px] font-bold text-gray-900 tracking-tight mb-3">主目标已确认</h2>
            <p className="text-[15px] text-gray-500 font-medium leading-relaxed mb-10 max-w-[240px]">
              你的训练计划和目标分数已经基于新岗位更新完毕。
            </p>

            <button onClick={() => onGo("today")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
              回到首页
            </button>
          </motion.div>
        </div>
      </div>
    );
  }

  return null;
};
