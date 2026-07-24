import React, { useState } from "react";
import { 
  ChevronLeft, Search, Sparkles, User, MapPin, 
  GraduationCap, Briefcase, Clock, Target, CheckCircle2, 
  AlertTriangle, ChevronRight, X, ArrowRight, ShieldCheck,
  Building2, BookOpen, AlertCircle, Save, Calendar, ArrowLeft, Edit3
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { getCaptureState } from "../../capture-state";

type FlowState = "welcome" | "hub" | "step_identity" | "step_eligibility" | "step_preferences" | "step_time" | "step_ability" | "confirmation";
const flowStates: readonly FlowState[] = ["welcome", "hub", "step_identity", "step_eligibility", "step_preferences", "step_time", "step_ability", "confirmation"];

export const OnboardingProfileWorkbenchPage = ({ onGo, onBack }: { onGo: (page: string) => void; onBack: () => void }) => {
  const [view, setView] = useState<FlowState>(() => getCaptureState(flowStates, "welcome"));
  
  // Form Data States (Mocked for interaction)
  const [examCount, setExamCount] = useState<"first" | "retake" | null>(null);
  const [workStatus, setWorkStatus] = useState<"employed" | "fulltime" | "student" | null>(null);
  
  const [majorSearch, setMajorSearch] = useState("");
  const [majorSelected, setMajorSelected] = useState(false);
  const [grassroots, setGrassroots] = useState<"none" | "village" | "support" | "teacher" | "west" | null>(null);
  const [workExp, setWorkExp] = useState<"none" | "under_2" | "over_2" | null>(null);

  const [region, setRegion] = useState<string>("北京");
  const [acceptRelocate, setAcceptRelocate] = useState<boolean>(false);

  const [workdayHours, setWorkdayHours] = useState(2.5);
  const [weekendHours, setWeekendHours] = useState(6.0);

  const [abilityKnown, setAbilityKnown] = useState<boolean | null>(null);

  // Helper: Top Navigation Bar
  const NavBar = ({ title, showSave = true, onBackClick }: { title: string, showSave?: boolean, onBackClick: () => void }) => (
    <div className="h-14 bg-white/80 backdrop-blur-xl border-b border-gray-100 flex items-center justify-between px-4 shrink-0 z-20 sticky top-0 mt-[48px]">
      <div className="flex items-center gap-3">
        <button onClick={onBackClick} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
        <span className="text-[16px] font-bold text-gray-900">{title}</span>
      </div>
      {showSave && (
        <button onClick={() => setView("hub")} className="text-[13px] font-bold text-indigo-500 bg-indigo-50 px-3 py-1.5 rounded-full active:scale-95 transition-transform">
          暂存并退出
        </button>
      )}
    </div>
  );

  // ---------------------------------------------------------------------------
  // 0. WELCOME (Choose Flow)
  // -------------------------------------------------------------------------
  if (view === "welcome") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans items-center justify-center px-8">
        <div className="h-[48px] w-full shrink-0 z-50 absolute top-0 pointer-events-none" />
        <button onClick={onBack} className="absolute top-[60px] left-4 p-2 text-gray-900"><ChevronLeft size={24}/></button>
        
        <div className="w-20 h-20 bg-white rounded-[24px] shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex items-center justify-center mb-8 border border-gray-50">
          <Sparkles size={36} className="text-indigo-500" />
        </div>
        <h1 className="text-[28px] font-bold text-gray-900 tracking-tight mb-3">建立备考档案</h1>
        <p className="text-[15px] text-gray-500 font-medium mb-12 text-center leading-relaxed">
          岸岸需要了解你的背景和目标，以提供准确的岗位推荐与定制化计划。
        </p>

        <div className="w-full flex flex-col gap-4">
          <button onClick={() => setView("step_identity")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform flex items-center justify-center gap-2">
            我是新用户，开始建档 <ArrowRight size={18} />
          </button>
          <button onClick={() => setView("hub")} className="w-full py-4 bg-white border border-gray-200 text-gray-900 rounded-[24px] font-bold text-[16px] active:scale-[0.98] transition-transform flex items-center justify-center gap-2">
            我是老用户，修改档案 <Edit3 size={18} />
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // HUB (Existing User / Saved State)
  // -------------------------------------------------------------------------
  if (view === "hub") {
    const modules = [
      { id: "step_identity", name: "身份与状态", desc: "首次备考 · 在职", status: "100%", icon: User, color: "text-blue-500", bg: "bg-blue-50" },
      { id: "step_eligibility", name: "报考资格", desc: "学历、专业、基层经历等", status: "待完善", icon: ShieldCheck, color: "text-orange-500", bg: "bg-orange-50" },
      { id: "step_preferences", name: "岗位偏好", desc: "北京 · 接受异地", status: "100%", icon: MapPin, color: "text-emerald-500", bg: "bg-emerald-50" },
      { id: "step_time", name: "学习时间", desc: "工作日2.5h · 周末6h", status: "100%", icon: Clock, color: "text-indigo-500", bg: "bg-indigo-50" },
      { id: "step_ability", name: "能力基础", desc: "未进行基础测评", status: "缺失", icon: Target, color: "text-rose-500", bg: "bg-rose-50" },
    ];

    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="h-14 bg-white/80 backdrop-blur-xl border-b border-gray-100 flex items-center px-4 shrink-0 z-20 mt-[48px]">
          <button onClick={onBack} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
          <span className="text-[16px] font-bold text-gray-900 ml-2">我的备考档案</span>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-6 pb-32 no-scrollbar">
          <div className="bg-indigo-50/50 border border-indigo-100/50 p-5 rounded-[24px] mb-8 flex items-start gap-3">
             <Sparkles size={18} className="text-indigo-500 shrink-0 mt-0.5" />
             <div>
               <h3 className="text-[14px] font-bold text-gray-900 mb-1">档案完善度影响岗位推荐</h3>
               <p className="text-[13px] text-gray-600 font-medium leading-relaxed">
                 你目前有两项关键信息待完善，这将导致岸岸无法为你过滤隐性限制条件的岗位。建议尽快补充。
               </p>
             </div>
          </div>

          <div className="flex flex-col gap-4">
            {modules.map(m => (
              <div key={m.id} className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex items-center justify-between">
                 <div className="flex items-center gap-4">
                    <div className={`w-12 h-12 rounded-full ${m.bg} flex items-center justify-center shrink-0`}>
                      <m.icon size={20} className={m.color} />
                    </div>
                    <div>
                      <h4 className="text-[15px] font-bold text-gray-900 mb-1">{m.name}</h4>
                      <p className="text-[12px] font-medium text-gray-500">{m.desc}</p>
                    </div>
                 </div>
                 <div className="flex flex-col items-end gap-2">
                    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-md ${m.status === "100%" ? "bg-gray-100 text-gray-500" : "bg-orange-50 text-orange-600"}`}>
                      {m.status}
                    </span>
                    <button onClick={() => setView(m.id as FlowState)} className="text-[13px] font-bold text-indigo-500 active:scale-95">修改</button>
                 </div>
              </div>
            ))}
          </div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
          <button onClick={() => setView("confirmation")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform flex items-center justify-center gap-2">
            查看整体档案评估 <ArrowRight size={18} />
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 1: IDENTITY
  // -------------------------------------------------------------------------
  if (view === "step_identity") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <NavBar title="1/5 身份与状态" onBackClick={() => setView("welcome")} />
        
        <div className="flex-1 overflow-y-auto px-6 pt-6 pb-32 no-scrollbar">
          <h2 className="text-[24px] font-bold text-gray-900 tracking-tight mb-2">了解你的基本状态</h2>
          <p className="text-[14px] text-gray-500 font-medium mb-8 leading-relaxed">当前仅支持国家公务员考试规划。岸岸会根据你的备考经验定制不同强度的训练节奏。</p>

          <div className="flex items-center gap-4 bg-white p-4 rounded-[20px] shadow-sm border border-gray-50 mb-8">
             <div className="w-14 h-14 bg-indigo-100 rounded-full flex items-center justify-center border-2 border-white shadow-sm overflow-hidden">
               <img src="https://api.dicebear.com/7.x/notionists/svg?seed=Felix&backgroundColor=e0e7ff" alt="avatar" className="w-full h-full object-cover" />
             </div>
             <div className="flex-1">
               <p className="text-[12px] text-gray-400 font-bold mb-0.5">微信昵称</p>
               <input type="text" defaultValue="林小鱼" className="w-full text-[16px] font-bold text-gray-900 outline-none bg-transparent border-b border-gray-200 focus:border-indigo-500 pb-1 transition-colors" />
             </div>
          </div>

          <h3 className="text-[15px] font-bold text-gray-900 mb-3 ml-1">你的备考经验</h3>
          <div className="grid grid-cols-2 gap-3 mb-8">
            <button onClick={() => setExamCount("first")} className={`p-4 rounded-[20px] text-left border-[1.5px] transition-all ${examCount === "first" ? "border-gray-900 bg-white shadow-md" : "border-transparent bg-white shadow-sm"}`}>
              <div className="flex justify-between items-center mb-1">
                <span className="text-[15px] font-bold text-gray-900">首次备考</span>
                {examCount === "first" && <CheckCircle2 size={18} className="text-gray-900" />}
              </div>
              <p className="text-[12px] text-gray-500 font-medium mt-2">从零开始建立基础</p>
            </button>
            <button onClick={() => setExamCount("retake")} className={`p-4 rounded-[20px] text-left border-[1.5px] transition-all ${examCount === "retake" ? "border-gray-900 bg-white shadow-md" : "border-transparent bg-white shadow-sm"}`}>
              <div className="flex justify-between items-center mb-1">
                <span className="text-[15px] font-bold text-gray-900">二战/多战</span>
                {examCount === "retake" && <CheckCircle2 size={18} className="text-gray-900" />}
              </div>
              <p className="text-[12px] text-gray-500 font-medium mt-2">重点突破弱项短板</p>
            </button>
          </div>

          <h3 className="text-[15px] font-bold text-gray-900 mb-3 ml-1">当前身份状态</h3>
          <div className="flex flex-col gap-3">
            {[
              { id: "employed", title: "在职备考", desc: "时间碎片化，需要极高效率" },
              { id: "fulltime", title: "全职备考", desc: "时间充足，需要系统性拉练" },
              { id: "student", title: "在校生", desc: "兼顾学业，弹性安排进度" },
            ].map(s => (
              <button key={s.id} onClick={() => setWorkStatus(s.id as any)} className={`p-4 rounded-[20px] text-left border-[1.5px] transition-all flex items-center justify-between ${workStatus === s.id ? "border-gray-900 bg-white shadow-md" : "border-transparent bg-white shadow-sm"}`}>
                <div>
                  <span className="text-[15px] font-bold text-gray-900 block mb-1">{s.title}</span>
                  <span className="text-[12px] text-gray-500 font-medium">{s.desc}</span>
                </div>
                <div className={`w-5 h-5 rounded-full border-[1.5px] flex items-center justify-center ${workStatus === s.id ? "border-gray-900" : "border-gray-300"}`}>
                  {workStatus === s.id && <div className="w-2.5 h-2.5 bg-gray-900 rounded-full" />}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
          <button disabled={!examCount || !workStatus} onClick={() => setView("step_eligibility")} className={`w-full py-4 rounded-[24px] font-bold text-[16px] transition-transform ${(!examCount || !workStatus) ? "bg-gray-100 text-gray-400" : "bg-gray-900 text-white shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98]"}`}>
            继续：填写报考资格
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 2: ELIGIBILITY
  // -------------------------------------------------------------------------
  if (view === "step_eligibility") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <NavBar title="2/5 报考资格" onBackClick={() => setView("step_identity")} />
        
        <div className="flex-1 overflow-y-auto px-5 pt-6 pb-40 no-scrollbar">
          <div className="bg-orange-50/50 border border-orange-100/50 p-4 rounded-[20px] mb-8 flex items-start gap-3">
             <AlertCircle size={16} className="text-orange-500 shrink-0 mt-0.5" />
             <p className="text-[13px] text-gray-700 font-medium leading-relaxed">
               这部分信息将决定你能否跨过岗位的硬性门槛。不确定的可以先标记“稍后确认”。
             </p>
          </div>

          {/* Major */}
          <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6">
             <h3 className="text-[15px] font-bold text-gray-900 mb-2">专业与学历</h3>
             <p className="text-[12px] text-gray-500 font-medium mb-4 leading-relaxed">专业是国考岗位的核心门槛，匹配度优先于地区偏好。</p>
             
             {!majorSelected ? (
               <div className="relative mb-4">
                 <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                 <input 
                   type="text" 
                   value={majorSearch}
                   onChange={e => setMajorSearch(e.target.value)}
                   placeholder="搜索具体专业名称..." 
                   className="w-full h-12 pl-10 pr-4 bg-[#F7F9FC] border border-gray-100 rounded-[12px] text-[14px] focus:outline-none focus:border-indigo-300 focus:bg-white transition-colors"
                 />
                 {majorSearch && (
                   <div className="absolute top-14 left-0 right-0 bg-white border border-gray-100 shadow-xl rounded-[16px] overflow-hidden z-20">
                      <button onClick={() => setMajorSelected(true)} className="w-full text-left px-4 py-3 hover:bg-gray-50 active:bg-gray-100 border-b border-gray-50">
                        <p className="text-[14px] font-bold text-gray-900">计算机科学与技术 <span className="text-gray-400 font-normal ml-1">080901</span></p>
                        <p className="text-[11px] font-bold text-indigo-500 mt-1">所属大类：工学 - 计算机类</p>
                      </button>
                      <button onClick={() => setMajorSelected(true)} className="w-full text-left px-4 py-3 hover:bg-gray-50 active:bg-gray-100">
                        <p className="text-[14px] font-bold text-gray-900">电子与计算机工程 <span className="text-gray-400 font-normal ml-1">080909</span></p>
                        <p className="text-[11px] font-bold text-indigo-500 mt-1">所属大类：工学 - 计算机类</p>
                      </button>
                   </div>
                 )}
               </div>
             ) : (
               <div className="mb-4 bg-indigo-50/50 border border-indigo-100 p-4 rounded-[16px] flex justify-between items-start">
                  <div>
                    <p className="text-[15px] font-bold text-gray-900 mb-1">计算机科学与技术</p>
                    <p className="text-[12px] font-bold text-indigo-600 bg-indigo-100/50 px-2 py-0.5 rounded-md inline-block">工学 - 计算机类</p>
                  </div>
                  <button onClick={() => {setMajorSelected(false); setMajorSearch("");}} className="text-[12px] font-bold text-gray-400 hover:text-gray-900">修改</button>
               </div>
             )}

             <div className="grid grid-cols-2 gap-3 mt-4">
                <div className="flex flex-col gap-1.5">
                  <label className="text-[12px] font-bold text-gray-400 ml-1">最高学历</label>
                  <select className="h-12 bg-[#F7F9FC] border border-gray-100 rounded-[12px] px-3 text-[14px] font-bold text-gray-900 focus:outline-none">
                    <option>本科</option>
                    <option>硕士研究生</option>
                    <option>博士研究生</option>
                  </select>
                </div>
                <div className="flex flex-col gap-1.5">
                  <label className="text-[12px] font-bold text-gray-400 ml-1">毕业年份</label>
                  <select className="h-12 bg-[#F7F9FC] border border-gray-100 rounded-[12px] px-3 text-[14px] font-bold text-gray-900 focus:outline-none">
                    <option>2024 (应届)</option>
                    <option>2023</option>
                    <option>2022及以前</option>
                  </select>
                </div>
             </div>
          </div>

          {/* Grassroots vs Normal Work */}
          <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6">
             <h3 className="text-[15px] font-bold text-gray-900 mb-2">服务基层项目经历</h3>
             <p className="text-[12px] text-gray-500 font-medium mb-4 leading-relaxed">
               包含大学生村官、三支一扶、特岗教师等。此项能为你解锁大量定向招录的特殊岗位，与普通工作经历严格区分。
             </p>

             <div className="flex flex-col gap-3 mb-5">
               <button onClick={() => setGrassroots("none")} className={`p-4 rounded-[16px] text-left border transition-all flex items-center justify-between ${grassroots === "none" ? "border-gray-900 bg-gray-50 shadow-sm" : "border-gray-100 bg-[#F7F9FC]"}`}>
                 <span className="text-[14px] font-bold text-gray-900">无服务基层项目经历</span>
                 {grassroots === "none" && <CheckCircle2 size={16} className="text-gray-900" />}
               </button>
               <button onClick={() => setGrassroots("village")} className={`p-4 rounded-[16px] text-left border transition-all flex flex-col ${grassroots === "village" ? "border-indigo-500 bg-indigo-50/50 shadow-sm" : "border-gray-100 bg-[#F7F9FC]"}`}>
                 <div className="flex items-center justify-between w-full mb-1">
                   <span className="text-[14px] font-bold text-gray-900">有基层项目经历</span>
                   {grassroots === "village" && <CheckCircle2 size={16} className="text-indigo-600" />}
                 </div>
                 <AnimatePresence>
                   {grassroots === "village" && (
                     <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} className="overflow-hidden w-full pt-3 mt-2 border-t border-indigo-100/50">
                        <select className="w-full h-10 mb-3 bg-white border border-gray-200 rounded-[10px] px-3 text-[13px] font-medium text-gray-900 focus:outline-none">
                          <option>大学生村官</option>
                          <option>三支一扶计划</option>
                          <option>农村特岗教师</option>
                          <option>西部计划</option>
                        </select>
                        <div className="flex gap-2">
                          <label className="flex items-center gap-2 text-[12px] font-medium text-gray-600 bg-white border border-gray-200 px-3 py-2 rounded-[10px] flex-1">
                            <input type="checkbox" className="accent-indigo-500" defaultChecked /> 已期满
                          </label>
                          <label className="flex items-center gap-2 text-[12px] font-medium text-gray-600 bg-white border border-gray-200 px-3 py-2 rounded-[10px] flex-1">
                            <input type="checkbox" className="accent-indigo-500" defaultChecked /> 有证明
                          </label>
                        </div>
                     </motion.div>
                   )}
                 </AnimatePresence>
               </button>
             </div>

             <div className="h-px bg-gray-50 mb-5" />

             <h3 className="text-[15px] font-bold text-gray-900 mb-2">普通工作经历</h3>
             <div className="flex gap-2">
                {["无经验", "2年以下", "2年以上"].map((e, i) => {
                  const id = i === 0 ? "none" : i === 1 ? "under_2" : "over_2";
                  return (
                    <button key={e} onClick={() => setWorkExp(id as any)} className={`flex-1 py-2.5 rounded-[12px] text-[13px] font-bold border transition-colors ${workExp === id ? "bg-gray-900 text-white border-gray-900 shadow-sm" : "bg-[#F7F9FC] text-gray-600 border-gray-100"}`}>
                      {e}
                    </button>
                  );
                })}
             </div>
          </div>

          <button className="w-full py-4 text-[14px] font-bold text-gray-400 bg-transparent border border-dashed border-gray-200 rounded-[24px] active:scale-95 transition-transform flex justify-center items-center gap-2">
            标记此页信息稍后确认
          </button>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40 flex gap-3">
          <button onClick={() => setView("step_preferences")} className="flex-1 py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
            继续：填写岗位偏好
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 3: PREFERENCES
  // -------------------------------------------------------------------------
  if (view === "step_preferences") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <NavBar title="3/5 岗位偏好" onBackClick={() => setView("step_eligibility")} />
        
        <div className="flex-1 overflow-y-auto px-5 pt-6 pb-40 no-scrollbar">
          <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6">
             <h3 className="text-[16px] font-bold text-gray-900 mb-2">首选工作地区</h3>
             <p className="text-[12px] text-gray-500 font-medium mb-4 leading-relaxed">如果首选地区未匹配到理想岗位，岸岸将为你寻找周边或相似分数段区域。</p>
             
             <div className="flex flex-wrap gap-2 mb-5">
               {["北京", "天津", "河北", "上海", "江苏", "浙江", "广东"].map(r => (
                 <button key={r} onClick={() => setRegion(r)} className={`px-4 py-2 rounded-full text-[13px] font-bold transition-colors border ${region === r ? "bg-gray-900 text-white border-gray-900 shadow-sm" : "bg-[#F7F9FC] text-gray-600 border-gray-100"}`}>
                   {r}
                 </button>
               ))}
             </div>

             <div className="flex items-center justify-between p-4 bg-[#F7F9FC] rounded-[16px] border border-gray-100">
                <div>
                  <h4 className="text-[14px] font-bold text-gray-900 mb-0.5">接受调剂或异地岗位</h4>
                  <p className="text-[11px] text-gray-500 font-medium">大幅增加符合条件的岗位基数</p>
                </div>
                <div onClick={() => setAcceptRelocate(!acceptRelocate)} className={`w-12 h-7 rounded-full flex items-center p-1 shrink-0 transition-colors cursor-pointer ${acceptRelocate ? "bg-green-500 justify-end" : "bg-gray-200 justify-start"}`}>
                  <div className="w-5 h-5 bg-white rounded-full shadow-sm" />
                </div>
             </div>
          </div>

          <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50">
             <h3 className="text-[16px] font-bold text-gray-900 mb-4">岗位层级接受度</h3>
             <div className="flex flex-col gap-3">
               {[
                 { level: "中央国家行政机关", checked: true },
                 { level: "省级以上机关", checked: true },
                 { level: "市（地）级机关", checked: true },
                 { level: "县（区）级及以下", checked: false },
               ].map(l => (
                 <label key={l.level} className="flex items-center gap-3 p-4 bg-[#F7F9FC] rounded-[16px] border border-gray-100">
                    <input type="checkbox" className="w-4 h-4 accent-gray-900" defaultChecked={l.checked} />
                    <span className="text-[14px] font-bold text-gray-800">{l.level}</span>
                 </label>
               ))}
             </div>
          </div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40 flex gap-3">
          <button onClick={() => setView("step_time")} className="flex-1 py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
            继续：填写学习时间
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 4: TIME
  // -------------------------------------------------------------------------
  if (view === "step_time") {
    // A simple reusable slider component UI
    const TimeSlider = ({ label, value, onChange }: { label: string, value: number, onChange: (v: number) => void }) => (
      <div className="mb-8">
        <div className="flex justify-between items-end mb-4">
          <h4 className="text-[15px] font-bold text-gray-900">{label}</h4>
          <span className="text-[20px] font-bold text-indigo-600 leading-none">{value.toFixed(1)} <span className="text-[12px] text-gray-400 font-medium">小时</span></span>
        </div>
        <div className="flex items-center gap-4 bg-[#F7F9FC] rounded-[20px] p-4 border border-gray-100">
          <button onClick={() => onChange(Math.max(0.5, value - 0.5))} className="w-8 h-8 rounded-full bg-white flex items-center justify-center text-gray-500 shadow-sm active:scale-95 border border-gray-50">-</button>
          <input 
            type="range" min="0.5" max="12" step="0.5" 
            value={value} 
            onChange={(e) => onChange(Number(e.target.value))}
            className="flex-1 h-2 bg-gray-200 rounded-full appearance-none accent-indigo-500"
          />
          <button onClick={() => onChange(Math.min(12, value + 0.5))} className="w-8 h-8 rounded-full bg-white flex items-center justify-center text-gray-900 shadow-sm active:scale-95 border border-gray-50">+</button>
        </div>
      </div>
    );

    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <NavBar title="4/5 学习时间" onBackClick={() => setView("step_preferences")} />
        
        <div className="flex-1 overflow-y-auto px-5 pt-6 pb-40 no-scrollbar">
          <div className="bg-white rounded-[24px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6">
             <p className="text-[13px] text-gray-500 font-medium mb-6 leading-relaxed">
               合理的排期比虚高的计划更有效。每天最少 0.5 小时，最高 12 小时。岸岸将据此分配每天的任务容量。
             </p>
             
             <TimeSlider label="工作日 (周一至周五) 日均可学" value={workdayHours} onChange={setWorkdayHours} />
             <div className="h-px bg-gray-50 mb-6" />
             <TimeSlider label="周末及节假日 日均可学" value={weekendHours} onChange={setWeekendHours} />
             
             {/* Quick fill */}
             <div className="flex gap-2">
               <button onClick={() => { setWorkdayHours(2); setWeekendHours(6); }} className="flex-1 py-2 bg-[#F7F9FC] text-gray-600 rounded-[12px] text-[12px] font-bold border border-gray-100 active:scale-95 transition-transform">标准在职 (2h/6h)</button>
               <button onClick={() => { setWorkdayHours(8); setWeekendHours(8); }} className="flex-1 py-2 bg-[#F7F9FC] text-gray-600 rounded-[12px] text-[12px] font-bold border border-gray-100 active:scale-95 transition-transform">全职备考 (8h/8h)</button>
             </div>
          </div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40 flex gap-3">
          <button onClick={() => setView("step_ability")} className="flex-1 py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
            继续：能力基础
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // STEP 5: ABILITY BASELINE
  // -------------------------------------------------------------------------
  if (view === "step_ability") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <NavBar title="5/5 能力基础" onBackClick={() => setView("step_time")} />
        
        <div className="flex-1 overflow-y-auto px-5 pt-6 pb-40 no-scrollbar">
          <h2 className="text-[20px] font-bold text-gray-900 mb-2 ml-1">了解你的起点</h2>
          <p className="text-[13px] text-gray-500 font-medium mb-6 ml-1 leading-relaxed">
            区分自报成绩与系统诊断。你提供的成绩仅作为初始锚点。
          </p>

          <div className="flex flex-col gap-4 mb-6">
            <button 
              onClick={() => setAbilityKnown(false)}
              className={`p-5 rounded-[24px] text-left border-[1.5px] transition-all bg-white ${abilityKnown === false ? "border-gray-900 shadow-md" : "border-gray-50 shadow-[0_4px_20px_rgba(0,0,0,0.02)]"}`}
            >
               <div className="flex justify-between items-start mb-2">
                 <div className="w-10 h-10 rounded-full bg-cyan-50 flex items-center justify-center shrink-0">
                   <Target size={18} className="text-cyan-500" />
                 </div>
                 {abilityKnown === false && <CheckCircle2 size={20} className="text-gray-900" />}
               </div>
               <h3 className="text-[16px] font-bold text-gray-900 mb-1">不清楚当前水平</h3>
               <p className="text-[12px] text-gray-500 font-medium">跳过填写，建档完成后由岸岸为你安排一次简短的基础摸底测评。</p>
            </button>

            <button 
              onClick={() => setAbilityKnown(true)}
              className={`p-5 rounded-[24px] text-left border-[1.5px] transition-all bg-white ${abilityKnown === true ? "border-indigo-500 shadow-md" : "border-gray-50 shadow-[0_4px_20px_rgba(0,0,0,0.02)]"}`}
            >
               <div className="flex justify-between items-start mb-2">
                 <div className="w-10 h-10 rounded-full bg-indigo-50 flex items-center justify-center shrink-0">
                   <BookOpen size={18} className="text-indigo-500" />
                 </div>
                 {abilityKnown === true && <CheckCircle2 size={20} className="text-indigo-600" />}
               </div>
               <h3 className="text-[16px] font-bold text-gray-900 mb-1">录入以往模考/真实成绩</h3>
               <p className="text-[12px] text-gray-500 font-medium mb-3">帮助系统更快校准你的弱点分布。</p>

               <AnimatePresence>
                 {abilityKnown && (
                   <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} className="overflow-hidden w-full pt-4 border-t border-gray-100">
                     <div className="flex flex-col gap-3">
                       <div className="flex gap-2">
                         <div className="flex-1 bg-[#F7F9FC] border border-gray-100 rounded-[16px] p-3">
                           <p className="text-[11px] font-bold text-gray-400 mb-1">行测总分 (估)</p>
                           <input type="text" placeholder="如: 65" className="w-full bg-transparent border-none text-[15px] font-bold text-gray-900 focus:outline-none placeholder:text-gray-300" />
                         </div>
                         <div className="flex-1 bg-[#F7F9FC] border border-gray-100 rounded-[16px] p-3">
                           <p className="text-[11px] font-bold text-gray-400 mb-1">申论总分 (估)</p>
                           <input type="text" placeholder="如: 60" className="w-full bg-transparent border-none text-[15px] font-bold text-gray-900 focus:outline-none placeholder:text-gray-300" />
                         </div>
                       </div>
                       <p className="text-[11px] text-gray-400 font-medium mt-1">注：更细致的模块表现（如言语、资料分析错题数）可在建档后通过导入试卷自动解析。</p>
                     </div>
                   </motion.div>
                 )}
               </AnimatePresence>
            </button>
          </div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
          <button 
            disabled={abilityKnown === null}
            onClick={() => setView("confirmation")} 
            className={`w-full py-4 rounded-[24px] font-bold text-[16px] transition-transform ${abilityKnown === null ? "bg-gray-100 text-gray-400" : "bg-gray-900 text-white shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98]"}`}
          >
            完成建档，查看评估
          </button>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // 6. CONFIRMATION (档案确认与评估页)
  // -------------------------------------------------------------------------
  if (view === "confirmation") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        <div className="flex-1 overflow-y-auto px-5 pt-6 pb-40 no-scrollbar">
           <motion.div
             initial={{ opacity: 0, scale: 0.95 }}
             animate={{ opacity: 1, scale: 1 }}
             transition={{ duration: 0.4, ease: "easeOut" }}
           >
              {/* Top Status */}
              <div className="flex flex-col items-center text-center mb-8 pt-4">
                 <div className="w-20 h-20 bg-green-50 rounded-full flex items-center justify-center mb-4 border border-green-100 shadow-sm">
                   <CheckCircle2 size={36} className="text-green-500" />
                 </div>
                 <h1 className="text-[26px] font-bold text-gray-900 tracking-tight mb-2">备考档案已生成</h1>
                 <p className="text-[14px] text-gray-500 font-medium">当前推荐可信度：<span className="text-green-600 font-bold">较高</span></p>
              </div>

              {/* Assessment Card */}
              <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.03)] border border-gray-50 mb-6 flex flex-col gap-5">
                 
                 <div>
                   <h3 className="text-[15px] font-bold text-gray-900 mb-1 flex items-center gap-1.5"><ShieldCheck size={16} className="text-indigo-500"/> 可靠判断</h3>
                   <p className="text-[13px] text-gray-600 font-medium leading-relaxed indent-5">
                     由于你的学历（硕士）和专业（计算机科学与技术）信息已确立，岸岸可以为你准确过滤 90% 的硬性门槛岗位。工作地点首选【北京】，接受调剂。
                   </p>
                 </div>
                 
                 <div className="h-px bg-gray-50" />

                 <div>
                   <h3 className="text-[15px] font-bold text-gray-900 mb-1 flex items-center gap-1.5"><AlertCircle size={16} className="text-orange-500"/> 缺失与风险</h3>
                   <p className="text-[13px] text-gray-600 font-medium leading-relaxed indent-5">
                     未填写“服务基层项目经历”具体证明状态，若要报考定向岗需后续补充；能力基线目前依赖自报预估值，尚未经系统真实校验。
                   </p>
                 </div>
                 
                 <div className="h-px bg-gray-50" />

                 <div className="bg-[#F7F9FC] p-4 rounded-[20px] border border-gray-100 relative">
                   <span className="absolute -top-2 left-4 bg-gray-900 text-white text-[10px] font-bold px-2 py-0.5 rounded shadow-sm">提前定位</span>
                   <p className="text-[13px] text-gray-600 font-medium leading-relaxed mt-1">
                     注：今年国考官方职位表尚未发布。目前的岗位空间是基于历年招录趋势、你的专业及地区投射出的大概定位，不代表最终确定岗位。
                   </p>
                 </div>
              </div>

              <h2 className="text-[16px] font-bold text-gray-900 mb-3 ml-1">下一步建议</h2>
              <div className="flex flex-col gap-3">
                 <button onClick={() => onGo("position_workbench")} className="bg-white rounded-[20px] p-4 shadow-sm border border-gray-100 active:scale-95 transition-transform flex items-center justify-between">
                   <div className="flex items-center gap-3">
                     <div className="w-10 h-10 bg-indigo-50 rounded-full flex items-center justify-center">
                       <MapPin size={18} className="text-indigo-500" />
                     </div>
                     <div className="text-left">
                       <p className="text-[15px] font-bold text-gray-900 mb-0.5">探索候选岗位空间</p>
                       <p className="text-[12px] text-gray-500 font-medium">查看 142 个历年相似可报岗位</p>
                     </div>
                   </div>
                   <ChevronRight size={18} className="text-gray-300" />
                 </button>

                 <button onClick={() => onGo("today")} className="bg-white rounded-[20px] p-4 shadow-sm border border-gray-100 active:scale-95 transition-transform flex items-center justify-between">
                   <div className="flex items-center gap-3">
                     <div className="w-10 h-10 bg-cyan-50 rounded-full flex items-center justify-center">
                       <Target size={18} className="text-cyan-600" />
                     </div>
                     <div className="text-left">
                       <p className="text-[15px] font-bold text-gray-900 mb-0.5">进行基础能力测评</p>
                       <p className="text-[12px] text-gray-500 font-medium">一套 30 分钟的卷子摸底</p>
                     </div>
                   </div>
                   <ChevronRight size={18} className="text-gray-300" />
                 </button>
              </div>
           </motion.div>
        </div>

        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
           <button onClick={() => onGo("today")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
             前往首页，开始今日学习
           </button>
        </div>
      </div>
    );
  }

  return null;
};
