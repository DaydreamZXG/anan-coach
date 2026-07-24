import React, { useState } from "react";
import { 
  ChevronLeft, 
  List, 
  Clock, 
  Search,
  CheckCircle2,
  AlertCircle,
  Sparkles,
  LayoutList,
  Quote,
  Save,
  ChevronDown,
  ArrowRight,
  PenTool,
  MessageSquare,
  FileText,
  MoreHorizontal
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { getCaptureState } from "../../capture-state";

type WorkbenchState = "writing" | "reading" | "result";
const workbenchStates: readonly WorkbenchState[] = ["writing", "reading", "result"];

export const EssayWorkbenchPage = ({ onGo, onBack }: { onGo: (page: any) => void; onBack: () => void }) => {
  const [view, setView] = useState<WorkbenchState>(() => getCaptureState(workbenchStates, "writing"));
  const [activeMaterial, setActiveMaterial] = useState(3);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Mock text content
  const materialText = `基层公共文化服务体系建设，是保障人民群众基本文化权益的重要途径。近年来，A市在推进基层文化建设中取得了一定成效，但也面临诸多挑战。部分乡镇文化站设施老化，如图书馆藏书陈旧、电子阅览室设备损坏率高。由于缺乏专项维护资金，许多文化设施处于闲置状态。\n\n同时，基层文化队伍专业人才匮乏，现有工作人员多为兼职，难以组织高质量的文化活动。此外，文化活动内容同质化严重，往往停留在简单的广场舞或送戏下乡，未能有效对接群众日益多元化、个性化的文化需求。\n\n有些乡村的农家书屋，门虽设而网不通，成了应付检查的“盆景”。针对这些现象，如何盘活存量资源、激发基层文化活力，成为当前亟待解决的课题。`;

  const answerText = `基层公共文化服务主要存在以下问题：\n\n一是基础设施老化闲置，维护资金不足。例如图书馆藏书陈旧、电子阅览室设备损坏。二是专业人才匮乏。工作人员多为兼职，缺乏专业的文化活动组织能力。三是文化活动内容单一、同质化严重。如仅有广场舞等，无法满足群众个性化和多元化的需求，部分设施成为应付检查的摆设。`;

  const handleSubmit = () => {
    setIsSubmitting(true);
    setTimeout(() => {
      setIsSubmitting(false);
      // Removed setView("result") because users should not enter AI grading
      // until they finish the whole exam.
    }, 1000);
  };

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans overflow-hidden">
      {/* MOCK STATUS BAR (Since StatusBar isn't exported from App.tsx) */}
      <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />

      {/* WECHAT CAPSULE MOCK */}
      <div className="absolute top-[48px] right-4 flex items-center z-50 pointer-events-none opacity-50">
        <div className="w-[87px] h-[32px] rounded-full bg-white/50 backdrop-blur-md border border-gray-200/50" />
      </div>

      <AnimatePresence mode="wait">
        {/* ================================================================================= */}
        {/* WRITING STATE */}
        {/* ================================================================================= */}
        {view === "writing" && (
          <motion.div 
            key="writing"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            className="flex flex-col h-full absolute inset-0 z-10"
          >
            {/* Header */}
            <div className="h-14 bg-white/80 backdrop-blur-xl border-b border-gray-100 flex items-center justify-between px-4 shrink-0 mt-[48px]">
              <div className="flex items-center gap-3">
                <button onClick={onBack} className="p-1 -ml-1 active:scale-95"><ChevronLeft size={22} className="text-gray-900" /></button>
                <div className="flex items-baseline gap-1.5">
                  <span className="text-[16px] font-bold text-gray-900">申论作答</span>
                  <span className="text-[12px] font-bold text-gray-400">2 / 5</span>
                </div>
              </div>
              <div className="flex items-center gap-3 mr-24"> {/* Leave space for capsule */}
                <div className="flex items-center gap-1.5 bg-[#F7F9FC] px-2.5 py-1 rounded-full border border-gray-100">
                  <Clock size={12} className="text-indigo-500" />
                  <span className="text-[12px] font-bold font-mono text-gray-700 mt-0.5">01:36:24</span>
                </div>
              </div>
            </div>

            {/* Material Strip */}
            <div className="bg-white px-4 py-2.5 shrink-0 flex items-center gap-3 overflow-x-auto no-scrollbar border-b border-gray-50 shadow-[0_2px_10px_rgba(0,0,0,0.01)]">
              {[1, 2, 3, 4].map((m) => (
                <button 
                  key={m} 
                  onClick={() => { setActiveMaterial(m); setView("reading"); }}
                  className={`flex items-center gap-1.5 shrink-0 px-3 py-1.5 rounded-full transition-all text-[13px] font-bold active:scale-95 ${
                    m === activeMaterial 
                      ? "bg-indigo-50 text-indigo-600 border border-indigo-100/50" 
                      : "bg-[#F7F9FC] text-gray-500 border border-transparent hover:bg-gray-100"
                  }`}
                >
                  <FileText size={14} className={m === activeMaterial ? "text-indigo-500" : "text-gray-400"} />
                  材料 {m}
                </button>
              ))}
              <span className="text-[11px] font-medium text-gray-400 ml-auto whitespace-nowrap px-2">
                已读到第 4 段
              </span>
            </div>

            <div className="flex-1 overflow-y-auto px-5 pt-5 pb-32 flex flex-col no-scrollbar">
              {/* Prompt */}
              <div className="mb-5">
                <p className="text-[16px] font-bold text-gray-900 leading-[1.6] mb-3">
                  请根据给定资料，概括基层公共文化服务中存在的主要问题。
                </p>
                <div className="flex flex-wrap gap-2">
                  <span className="bg-white border border-gray-100 text-gray-500 px-2 py-0.5 rounded-[6px] text-[11px] font-bold shadow-sm">概括准确</span>
                  <span className="bg-white border border-gray-100 text-gray-500 px-2 py-0.5 rounded-[6px] text-[11px] font-bold shadow-sm">条理清晰</span>
                  <span className="bg-white border border-gray-100 text-gray-500 px-2 py-0.5 rounded-[6px] text-[11px] font-bold shadow-sm">不超过 300 字</span>
                </div>
              </div>

              {/* Editor */}
              <div className="flex-1 bg-white rounded-[24px] shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-gray-50 flex flex-col overflow-hidden">
                <div className="p-5 flex-1 relative">
                  <textarea 
                    className="w-full h-full resize-none outline-none text-[15px] leading-[1.8] text-gray-800 tracking-wide bg-transparent"
                    defaultValue={answerText}
                    placeholder="在此输入作答内容..."
                  />
                </div>
                
                {/* Editor Footer Tools */}
                <div className="h-12 bg-[#F7F9FC]/50 border-t border-gray-50 flex items-center justify-between px-4">
                  <div className="flex gap-4">
                    <button className="text-gray-400 hover:text-gray-600 flex items-center gap-1 active:scale-95 transition-colors">
                      <LayoutList size={16} /><span className="text-[12px] font-bold">提纲</span>
                    </button>
                    <button className="text-gray-400 hover:text-gray-600 flex items-center gap-1 active:scale-95 transition-colors">
                      <Quote size={16} /><span className="text-[12px] font-bold">引用</span>
                    </button>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[11px] font-medium text-gray-400">刚刚已保存</span>
                    <span className="text-[12px] font-bold text-gray-900">186 <span className="text-gray-400 font-medium">/ 300</span></span>
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-center gap-1.5 mt-5">
                <div className="w-1.5 h-1.5 rounded-full bg-gray-300" />
                <span className="text-[11px] font-medium text-gray-400">整卷模考 · AI 提示已静默</span>
              </div>
            </div>

            {/* Bottom Submit */}
            <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
              <button 
                onClick={handleSubmit}
                disabled={isSubmitting}
                className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-all flex justify-center items-center gap-2"
              >
                {isSubmitting ? "正在保存..." : "保存并下一题"}
              </button>
            </div>
          </motion.div>
        )}

        {/* ================================================================================= */}
        {/* READING STATE */}
        {/* ================================================================================= */}
        {view === "reading" && (
          <motion.div 
            key="reading"
            initial={{ opacity: 0, y: 50 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 50 }}
            className="flex flex-col h-full absolute inset-0 z-20 bg-white"
          >
            {/* Header */}
            <div className="h-14 border-b border-gray-100 flex items-center justify-between px-4 shrink-0 mt-[48px]">
              <div className="flex items-center gap-2">
                <span className="text-[16px] font-bold text-gray-900 ml-1">材料 {activeMaterial} / 4</span>
              </div>
              <div className="flex items-center gap-3 mr-24">
                <button className="text-gray-400 p-1.5 bg-gray-50 rounded-full active:scale-95"><Search size={16} /></button>
                <button onClick={() => setView("writing")} className="text-gray-500 font-bold text-[13px] px-3 py-1.5 bg-gray-50 rounded-full active:scale-95">关闭</button>
              </div>
            </div>

            {/* Reader Content */}
            <div className="flex-1 overflow-y-auto px-6 py-8 pb-32 no-scrollbar text-[16px] leading-[1.8] text-gray-800 tracking-wide selection:bg-cyan-100">
              {materialText.split('\n\n').map((para, idx) => (
                <div key={idx} className="relative mb-6">
                  <span className="absolute -left-5 top-1 text-gray-300 font-mono text-[11px] font-bold select-none">{idx + 1}</span>
                  <p className="indent-8">
                    {idx === 1 ? (
                      <span className="relative group">
                        <span className="bg-cyan-50 text-cyan-900 border-b border-cyan-200">
                          {para.substring(0, 48)}
                        </span>
                        {/* Mock selection tooltip */}
                        <div className="absolute -top-12 left-1/2 -translate-x-1/2 bg-gray-900 text-white rounded-[12px] px-3 py-2 flex items-center gap-3 shadow-xl whitespace-nowrap z-50">
                           <span className="text-[12px] font-bold flex items-center gap-1.5"><PenTool size={12}/>划线</span>
                           <div className="w-px h-3 bg-gray-700" />
                           <span className="text-[12px] font-bold flex items-center gap-1.5"><LayoutList size={12}/>加入提纲</span>
                           <div className="w-px h-3 bg-gray-700" />
                           <span className="text-[12px] font-bold flex items-center gap-1.5"><Quote size={12}/>复制</span>
                           <div className="absolute top-full left-1/2 -translate-x-1/2 border-[6px] border-transparent border-t-gray-900" />
                        </div>
                        {para.substring(48)}
                      </span>
                    ) : (
                      para
                    )}
                  </p>
                </div>
              ))}
            </div>

            {/* Floating Action */}
            <div className="absolute bottom-8 left-1/2 -translate-x-1/2 z-40">
              <button onClick={() => setView("writing")} className="px-8 py-3.5 bg-gray-900 text-white rounded-full font-bold text-[15px] shadow-[0_8px_30px_rgba(0,0,0,0.2)] active:scale-95 transition-transform flex items-center gap-2">
                <ChevronDown size={18} /> 返回作答
              </button>
            </div>
          </motion.div>
        )}

        {/* ================================================================================= */}
        {/* GRADING RESULT STATE */}
        {/* ================================================================================= */}
        {view === "result" && (
          <motion.div 
            key="result"
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex flex-col h-full absolute inset-0 z-30 bg-[#F7F9FC]"
          >
            <div className="h-14 bg-white/50 backdrop-blur-xl border-b border-gray-100 flex items-center justify-between px-4 shrink-0 mt-[48px] z-10 sticky top-0">
              <div className="flex items-center gap-3">
                <button onClick={() => setView("writing")} className="p-1 -ml-1 active:scale-95"><ChevronLeft size={22} className="text-gray-900" /></button>
                <span className="text-[16px] font-bold text-gray-900">AI 批改复盘</span>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto px-5 pt-6 pb-[180px] flex flex-col gap-6 no-scrollbar relative">
              
              {/* Score Card */}
              <div className="bg-white rounded-[32px] p-8 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 relative overflow-hidden flex flex-col items-center shrink-0">
                 <div className="absolute top-0 right-0 w-40 h-40 bg-indigo-50 rounded-full blur-3xl -translate-y-1/2 translate-x-1/3" />
                 <div className="relative z-10 flex flex-col items-center w-full">
                   <p className="text-[13px] font-bold text-gray-400 mb-2">综合得分</p>
                   <div className="flex items-baseline gap-1 mb-4 pt-1">
                     <span className="text-[54px] font-bold text-gray-900 tracking-tighter leading-tight">68</span>
                     <span className="text-[18px] font-bold text-gray-400">/ 100</span>
                   </div>
                   <div className="flex items-center gap-1.5 bg-indigo-50 px-3 py-1.5 rounded-md mb-4">
                     <CheckCircle2 size={14} className="text-indigo-500" />
                     <span className="text-[12px] font-bold text-indigo-600">评分可信度较高</span>
                   </div>
                   <p className="text-[11px] text-gray-400 font-medium text-center leading-[1.8] pb-1">
                     由多模型独立评分并交叉校验<br/>本评分用于训练反馈，不等同于官方阅卷结果
                   </p>
                 </div>
              </div>

              {/* Dimensions */}
              <div>
                <h3 className="text-[17px] font-bold text-gray-900 mb-4 ml-1">得分维度</h3>
                <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex flex-col gap-4">
                  {[
                    { name: "要点覆盖", score: "14 / 20", width: "70%" },
                    { name: "结构层次", score: "8 / 10", width: "80%" },
                    { name: "材料依据", score: "12 / 15", width: "80%" },
                    { name: "表达准确", score: "6 / 10", width: "60%" },
                    { name: "字数与格式", score: "5 / 5", width: "100%" }
                  ].map(d => (
                    <div key={d.name} className="flex items-center gap-4">
                      <span className="text-[13px] font-bold text-gray-600 w-[68px]">{d.name}</span>
                      <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                        <div className="h-full bg-gray-900 rounded-full" style={{ width: d.width }} />
                      </div>
                      <span className="text-[12px] font-bold text-gray-900 w-[42px] text-right">{d.score}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Missing Points */}
              <div>
                <h3 className="text-[17px] font-bold text-gray-900 mb-4 ml-1">证据反馈</h3>
                <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex flex-col gap-4">
                   <div className="flex gap-3 mb-2">
                     <span className="text-[12px] font-bold text-green-600 bg-green-50 px-2 py-1 rounded-[8px] flex items-center gap-1"><CheckCircle2 size={12}/> 命中要点 4 个</span>
                     <span className="text-[12px] font-bold text-red-500 bg-red-50 px-2 py-1 rounded-[8px] flex items-center gap-1"><AlertCircle size={12}/> 遗漏要点 2 个</span>
                   </div>
                   
                   <div className="bg-[#F7F9FC] rounded-[16px] p-4 border border-gray-100 relative overflow-hidden">
                      <div className="absolute left-0 top-0 bottom-0 w-1 bg-red-400" />
                      <p className="text-[14px] font-bold text-gray-900 mb-1.5 ml-2">未能对接群众多元化需求</p>
                      <p className="text-[13px] text-gray-500 font-medium leading-[1.6] mb-3 ml-2">
                        原答案仅表述了“活动内容单一”，未结合材料明确指出导致该现象的深层原因是供需错位。
                      </p>
                      <button className="text-[12px] font-bold text-indigo-500 ml-2 bg-indigo-50 px-2.5 py-1 rounded-md active:scale-95 transition-transform flex items-center gap-1 w-fit">
                        定位至 材料二 第 2 段 <ArrowRight size={12} />
                      </button>
                   </div>
                </div>
              </div>

              {/* Rewrite Card */}
              <div>
                <h3 className="text-[17px] font-bold text-gray-900 mb-4 ml-1">表达升格</h3>
                <div className="flex flex-col">
                   <div className="bg-[#FFF5F5] p-5 rounded-t-[24px] rounded-b-[8px] border border-red-50 mb-1.5">
                      <span className="text-red-400 text-[11px] font-bold mb-2 block">原句提取</span>
                      <p className="text-red-900/80 text-[14px] leading-relaxed font-medium">三是文化活动内容单一、同质化严重。如仅有广场舞等。</p>
                   </div>
                   <div className="bg-cyan-50/50 p-5 rounded-b-[24px] rounded-t-[8px] border border-cyan-100/50 relative">
                      <span className="text-cyan-600 text-[11px] font-bold mb-2 block flex items-center gap-1"><Sparkles size={12}/>建议改写</span>
                      <p className="text-cyan-900 text-[14px] font-bold leading-relaxed mb-4">三是文化活动同质化严重，供需错位。仅停留于广场舞等简单形式，未对接群众多元化、个性化需求。</p>
                      <div className="border-t border-cyan-100 pt-3">
                        <p className="text-cyan-700/80 text-[12px] font-medium leading-relaxed">
                          改动解析：原句概括过于表面。补充“供需错位”和“多元化需求”能更精准踩中采分点，符合官方通报表达习惯。
                        </p>
                      </div>
                   </div>
                </div>
              </div>
            </div>

            {/* Bottom Actions */}
            <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
              <div className="flex gap-3 mb-3">
                <button onClick={() => setView("writing")} className="flex-1 py-4 bg-[#F7F9FC] text-gray-700 rounded-[20px] font-bold text-[14px] active:scale-[0.98] transition-transform border border-gray-100 flex items-center justify-center gap-1.5">
                   再写一次
                </button>
                <button className="flex-1 py-4 bg-[#F7F9FC] text-gray-700 rounded-[20px] font-bold text-[14px] active:scale-[0.98] transition-transform border border-gray-100 flex items-center justify-center gap-1.5">
                   加入明日专项
                </button>
              </div>
              <button onClick={() => onGo("coach")} className="w-full py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] shadow-[0_4px_15px_rgba(0,0,0,0.1)] active:scale-[0.98] transition-transform flex items-center justify-center gap-2">
                <Sparkles size={16} className="text-white/80" /> 带着本题问岸岸
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
