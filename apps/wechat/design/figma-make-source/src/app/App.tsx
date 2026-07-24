import React, { useState, useEffect } from "react";
import {
  Battery,
  Wifi,
  Signal,
  Check,
  ChevronRight,
  BookOpen,
  Target,
  Trophy,
  Brain,
  MessageSquare,
  User,
  Settings,
  ChevronLeft,
  Search,
  ArrowRight,
  ArrowLeft,
  Camera,
  FileText,
  Sparkles,
  Award,
  Map,
  Clock,
  ChevronDown,
  ChevronUp,
  Download,
  Shield,
  Trash2,
  Bell,
  AlertCircle,
  BarChart2,
  PieChart,
  Home,
  CloudRain,
  Coffee,
  CalendarDays,
  CheckCircle2,
  Play,
  RefreshCw,
  X,
  Edit3,
  Book,
  PenTool,
  Layers,
  Key,
  Headphones,
  Info,
  Pause,
  List,
  Bookmark,
  MinusCircle,
  PlusCircle,
  Undo2,
  Redo2,
  Eraser,
  Trash,
  XCircle,
  AlertTriangle,
  ThumbsUp,
  ThumbsDown
} from "lucide-react";
import { EssayWorkbenchPage } from "./components/EssayWorkbenchPage";
import { PositionWorkbenchPage } from "./components/PositionWorkbenchPage";
import { LibraryWorkbenchPage } from "./components/LibraryWorkbenchPage";
import { PhotoSearchWorkbenchPage } from "./components/PhotoSearchWorkbenchPage";
import { OnboardingProfileWorkbenchPage } from "./components/OnboardingProfileWorkbenchPage";
import { motion, AnimatePresence } from "motion/react";
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  BarChart,
  Bar,
  Cell,
} from "recharts";

// ═══════════════ TYPES ═══════════════════════════════════════════════════════
type NavTab = "today" | "ability" | "coach" | "profile";
type Page =
  | "login"
  | "onboarding"
  | "today"
  | "ability"
  | "coach"
  | "profile"
  | "practice"
  | "photo"
  | "essay"
  | "position"
  | "positionDetail"
  | "placeholder"
  | "result_summary"
  | "result_review"
  | "result_completed"
  | "task_basis"
  | "weekly_plan"
  | "today_adjustment"
  | "daily_settlement"
  | "essay_workbench"
  | "position_workbench"
  | "library_workbench"
  | "photo_search"
  | "onboarding_profile";

const pageValues = new Set<Page>([
  "login",
  "onboarding",
  "today",
  "ability",
  "coach",
  "profile",
  "practice",
  "photo",
  "essay",
  "position",
  "positionDetail",
  "placeholder",
  "result_summary",
  "result_review",
  "result_completed",
  "task_basis",
  "weekly_plan",
  "today_adjustment",
  "daily_settlement",
  "essay_workbench",
  "position_workbench",
  "library_workbench",
  "photo_search",
  "onboarding_profile",
]);

const requestedPage = new URLSearchParams(window.location.search).get("page");
const initialPage: Page =
  requestedPage && pageValues.has(requestedPage as Page)
    ? (requestedPage as Page)
    : "onboarding_profile";

// ═══════════════ MOCK DATA ════════════════════════════════════════════════════
const radarData = [
  { subject: "言语理解", score: 85 },
  { subject: "数量关系", score: 45 },
  { subject: "判断推理", score: 78 },
  { subject: "资料分析", score: 92 },
  { subject: "常识判断", score: 65 },
];

const trendData = [
  { day: "周一", correct: 65 },
  { day: "周二", correct: 68 },
  { day: "周三", correct: 62 },
  { day: "周四", correct: 75 },
  { day: "周五", correct: 72 },
  { day: "周六", correct: 78 },
  { day: "周日", correct: 82 },
];

const wrongData = [
  { name: "数量", count: 28 },
  { name: "常识", count: 15 },
  { name: "言语", count: 8 },
  { name: "判断", count: 5 },
  { name: "资料", count: 2 },
];

// ═══════════════ COMPONENTS ══════════════════════════════════════════════════
const StatusBar = ({ dark = false }: { dark?: boolean }) => {
  const color = dark ? "text-gray-900" : "text-gray-900";
  return (
    <div className={`flex justify-between items-center px-6 py-3 text-[14px] font-semibold ${color} z-50`}>
      <span>9:41</span>
      <div className="flex gap-2 items-center">
        <Signal size={16} strokeWidth={2.5} />
        <Wifi size={16} strokeWidth={2.5} />
        <Battery size={20} strokeWidth={2} />
      </div>
    </div>
  );
};

const WechatCapsule = ({ dark = false }: { dark?: boolean }) => {
  const bg = "bg-white/50 backdrop-blur-md";
  const border = "border border-gray-200/50";
  const iconColor = "text-gray-900";

  return (
    <div className="absolute top-[48px] right-4 flex items-center z-50">
      <div className={`flex items-center h-8 rounded-full px-3 gap-3 ${bg} ${border} shadow-sm`}>
        <div className="flex gap-1">
          <div className={`w-1.5 h-1.5 rounded-full bg-gray-900`} />
          <div className={`w-1.5 h-1.5 rounded-full bg-gray-900`} />
          <div className={`w-1.5 h-1.5 rounded-full bg-gray-900`} />
        </div>
        <div className={`w-px h-4 bg-gray-300`} />
        <div className="relative">
          <div className={`w-4 h-4 rounded-full border-[1.5px] border-gray-900 flex items-center justify-center`}>
            <div className={`w-1.5 h-1.5 rounded-full bg-gray-900 absolute`} style={{ transform: "translate(0.5px, 0.5px)" }} />
          </div>
        </div>
      </div>
    </div>
  );
};

const BottomNav = ({ active, onNav }: { active: NavTab; onNav: (tab: NavTab) => void }) => {
  const tabs = [
    { id: "today", icon: Home, label: "今日" },
    { id: "ability", icon: BarChart2, label: "能力" },
    { id: "coach", icon: Brain, label: "岸岸" },
    { id: "profile", icon: User, label: "我的" },
  ] as const;

  return (
    <div className="pb-8 pt-3 px-6 bg-white/80 backdrop-blur-2xl border-t border-gray-100 flex justify-between items-center relative z-40">
      {tabs.map((tab) => {
        const isActive = active === tab.id;
        return (
          <button
            key={tab.id}
            onClick={() => onNav(tab.id)}
            className="flex flex-col items-center gap-1.5 relative w-16"
          >
            {isActive && tab.id === "coach" && (
              <div className="absolute -top-3 w-10 h-10 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 rounded-full blur-xl opacity-40" />
            )}
            <tab.icon
              size={24}
              strokeWidth={isActive ? 2.5 : 2}
              className={`transition-all duration-300 z-10 ${
                isActive
                  ? tab.id === "coach"
                    ? "text-purple-600 drop-shadow-md scale-110"
                    : "text-gray-900 scale-110"
                  : "text-gray-400"
              }`}
            />
            <span
              className={`text-[10px] font-medium transition-all duration-300 ${
                isActive
                  ? tab.id === "coach"
                    ? "text-purple-600"
                    : "text-gray-900"
                  : "text-gray-400"
              }`}
            >
              {tab.label}
            </span>
          </button>
        );
      })}
    </div>
  );
};

// ═══════════════ PAGES ═══════════════════════════════════════════════════════

const LoginPage = ({ onLogin }: { onLogin: () => void }) => {
  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
      {/* Abstract Background */}
      <div className="absolute top-[-10%] right-[-20%] w-[350px] h-[350px] bg-gradient-to-br from-indigo-300/40 via-purple-300/40 to-pink-300/40 rounded-full blur-3xl opacity-60" />
      <div className="absolute bottom-[-10%] left-[-20%] w-[350px] h-[350px] bg-gradient-to-br from-blue-200/40 to-teal-200/40 rounded-full blur-3xl opacity-60" />

      <StatusBar />
      
      <div className="flex-1 flex flex-col justify-center px-8 relative z-10">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease: "easeOut" }}
          className="flex flex-col items-center text-center"
        >
          <div className="w-24 h-24 bg-white shadow-[0_8px_30px_rgba(0,0,0,0.04)] rounded-[2rem] flex items-center justify-center mb-8 relative">
            <div className="absolute inset-0 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 rounded-[2rem] opacity-10" />
            <Brain size={48} className="text-gray-900" strokeWidth={1.5} />
          </div>
          <h1 className="text-4xl font-bold text-gray-900 tracking-tight mb-3">岸岸备考</h1>
          <p className="text-gray-500 text-[15px] font-medium mb-12 max-w-[240px] leading-relaxed">
            基于认知科学的 AI 备考教练
            <br />为你规划最快的上岸路径
          </p>

          <button
            onClick={onLogin}
            className="w-full h-14 bg-gray-900 text-white rounded-[20px] font-semibold text-[16px] shadow-lg shadow-gray-900/20 active:scale-[0.98] transition-transform flex items-center justify-center gap-2"
          >
            微信一键登录
            <ArrowRight size={18} />
          </button>
          
          <div className="mt-6 flex items-center gap-2 text-gray-400 text-[12px]">
            <Check size={14} />
            <span>登录即代表同意用户协议与隐私政策</span>
          </div>
        </motion.div>
      </div>
    </div>
  );
};

const OnboardingPage = ({ onComplete }: { onComplete: () => void }) => {
  const [step, setStep] = useState(0);

  const steps = [
    { title: "你的目标地区是？", desc: "岸岸会据此分析历年分数线", opts: ["北京", "上海", "广东", "江苏", "浙江", "山东", "其他"] },
    { title: "目标岗位类型？", desc: "不同岗位侧重点不同", opts: ["综合管理", "行政执法", "专业技术", "乡镇基层", "暂未确定"] },
    { title: "每天能投入多少时间？", desc: "合理规划，避免焦虑", opts: ["2小时以内", "2-4小时", "4-6小时", "全职备考"] }
  ];

  const current = steps[step];

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
       <div className="absolute top-[-20%] left-[-10%] w-[300px] h-[300px] bg-gradient-to-br from-indigo-200/30 to-purple-200/30 rounded-full blur-3xl" />
      <StatusBar />
      
      <div className="px-6 pt-12 flex-1 flex flex-col relative z-10">
        <div className="flex gap-2 mb-8">
          {steps.map((_, i) => (
            <div key={i} className="h-1.5 flex-1 rounded-full bg-gray-200 overflow-hidden">
              <div 
                className="h-full bg-gray-900 rounded-full transition-all duration-500"
                style={{ width: i <= step ? "100%" : "0%" }}
              />
            </div>
          ))}
        </div>

        <motion.div
          key={step}
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          className="flex-1"
        >
          <h2 className="text-3xl font-bold text-gray-900 mb-2">{current.title}</h2>
          <p className="text-gray-500 text-[15px] mb-8">{current.desc}</p>
          
          <div className="flex flex-col gap-3">
            {current.opts.map(opt => (
              <button 
                key={opt}
                onClick={() => {
                  if (step < steps.length - 1) setStep(step + 1);
                  else onComplete();
                }}
                className="w-full p-4 bg-white rounded-[24px] shadow-[0_8px_30px_rgba(0,0,0,0.04)] text-left font-medium text-gray-900 text-[16px] hover:bg-gray-50 active:scale-[0.98] transition-all flex items-center justify-between"
              >
                {opt}
                <ChevronRight size={20} className="text-gray-400" />
              </button>
            ))}
          </div>
        </motion.div>
      </div>
    </div>
  );
};

const TaskBasisPage = ({ onGo, onBack }: { onGo: (page: Page) => void; onBack: () => void }) => {

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative">
      <StatusBar />
      <div className="absolute top-[48px] left-4 z-50">
        <button onClick={onBack} className="w-8 h-8 rounded-full bg-white/50 backdrop-blur-md border border-gray-200/50 flex items-center justify-center active:scale-95 shadow-sm">
          <ChevronLeft size={20} className="text-gray-900" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 pt-[104px] pb-32 flex flex-col gap-8 no-scrollbar">
        {/* Header Section */}
        <div>
          <span className="text-[12px] font-bold text-gray-500 bg-gray-200/50 px-2.5 py-1 rounded-md mb-4 inline-block">资料分析 · 约30分钟</span>
          <h1 className="text-[26px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-3">为什么今天练这个</h1>
          <p className="text-[15px] text-gray-500 leading-relaxed font-medium">这不是让你多刷一组题，而是专门验证一个近期反复出现的问题。</p>
        </div>

        {/* An'an Judgment Card */}
        <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] relative overflow-hidden border border-indigo-50/50">
          <div className="absolute inset-0 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 opacity-[0.03]" />
          <div className="relative z-10">
             <div className="flex items-center gap-2 mb-4">
               <Sparkles size={18} className="text-indigo-500" />
               <h3 className="font-bold text-[17px] text-gray-900">岸岸为什么这样安排</h3>
             </div>
             <p className="text-[15px] text-gray-900 font-bold leading-relaxed mb-5">你会列式，但遇到大数容易直接精算。最近两次都因此超时，今天先验证能不能在60秒内主动使用截位估算。</p>
             
             <ul className="flex flex-col gap-3 mb-5">
                <li className="flex items-start gap-2 text-[14px] text-gray-600 font-medium">
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-400 mt-2 shrink-0" />
                  近7天出现2次同类超时
                </li>
                <li className="flex items-start gap-2 text-[14px] text-gray-600 font-medium">
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-400 mt-2 shrink-0" />
                  草稿中多次出现完整大数除法
                </li>
                <li className="flex items-start gap-2 text-[14px] text-gray-600 font-medium">
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-400 mt-2 shrink-0" />
                  当前判断仍需复测确认
                </li>
             </ul>

             <AnimatePresence>
               {expanded && (
                 <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden mb-5">
                    <div className="bg-[#F7F9FC] rounded-[20px] p-4 flex flex-col gap-4">
                      <div>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[13px] font-bold text-gray-900">10月23日 · 模考练习</span>
                          <span className="text-[11px] text-orange-600 font-bold bg-orange-50 px-2 py-0.5 rounded-md">耗时102秒</span>
                        </div>
                        <p className="text-[13px] text-gray-500 leading-relaxed font-medium">在“2021年全国进口额比重”题中，列式正确但计算过程没有进行四舍五入。</p>
                      </div>
                      <div className="h-px bg-gray-200/60" />
                      <div>
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[13px] font-bold text-gray-900">10月21日 · 专项测验</span>
                          <span className="text-[11px] text-orange-600 font-bold bg-orange-50 px-2 py-0.5 rounded-md">耗时95秒</span>
                        </div>
                        <p className="text-[13px] text-gray-500 leading-relaxed font-medium">在“第一季度占比测算”中，草稿中出现了5位数除法计算。</p>
                      </div>
                    </div>
                 </motion.div>
               )}
             </AnimatePresence>

             <button onClick={() => setExpanded(!expanded)} className="text-[13px] font-bold text-indigo-500 flex items-center gap-1 active:scale-95 transition-transform bg-indigo-50/50 px-3 py-1.5 rounded-full">
               {expanded ? "收起完整依据" : "查看完整依据"} <ChevronDown size={14} className={`transition-transform duration-300 ${expanded ? "rotate-180" : ""}`} />
             </button>
          </div>
        </div>

        {/* How to Practice */}
        <div>
          <h3 className="text-[18px] font-bold text-gray-900 tracking-tight mb-4 ml-1">这30分钟怎么练</h3>
          <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex flex-col gap-6 relative">
             <div className="absolute left-8 top-10 bottom-10 w-0.5 bg-gray-100 z-0" />
             
             <div className="flex gap-4 relative z-10">
                <div className="w-5 h-5 rounded-full bg-gray-900 text-white flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5 shadow-sm">1</div>
                <div>
                   <h4 className="text-[15px] font-bold text-gray-900 mb-1">5题识别</h4>
                   <p className="text-[13px] text-gray-500 font-medium leading-relaxed">判断哪些题可以估算，建立直觉。</p>
                </div>
             </div>
             
             <div className="flex gap-4 relative z-10">
                <div className="w-5 h-5 rounded-full bg-gray-900 text-white flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5 shadow-sm">2</div>
                <div>
                   <h4 className="text-[15px] font-bold text-gray-900 mb-1">8题应用</h4>
                   <p className="text-[13px] text-gray-500 font-medium leading-relaxed">强制要求在60秒内完成截位计算。</p>
                </div>
             </div>

             <div className="flex gap-4 relative z-10">
                <div className="w-5 h-5 rounded-full bg-indigo-500 text-white flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5 shadow-md shadow-indigo-500/30">3</div>
                <div>
                   <h4 className="text-[15px] font-bold text-gray-900 mb-1">2题验证</h4>
                   <p className="text-[13px] text-gray-500 font-medium leading-relaxed">检查是否能稳定使用，而不是偶然做对。</p>
                </div>
             </div>
          </div>
        </div>

        {/* Success Criteria */}
        <div>
          <h3 className="text-[18px] font-bold text-gray-900 tracking-tight mb-4 ml-1">今天练成什么算有效</h3>
          <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex flex-col gap-4">
             <div className="flex items-center gap-3">
                <CheckCircle2 size={18} className="text-green-500 shrink-0" />
                <span className="text-[14px] text-gray-800 font-bold">能主动识别可估算题</span>
             </div>
             <div className="flex items-center gap-3">
                <CheckCircle2 size={18} className="text-green-500 shrink-0" />
                <span className="text-[14px] text-gray-800 font-bold">平均用时控制在60秒附近</span>
             </div>
             <div className="flex items-center gap-3">
                <CheckCircle2 size={18} className="text-green-500 shrink-0" />
                <span className="text-[14px] text-gray-800 font-bold">草稿不再出现无必要的完整精算</span>
             </div>
             
             <div className="mt-2 pt-4 border-t border-gray-100 flex gap-2.5 bg-gray-50/50 -mx-6 -mb-6 p-5 rounded-b-[32px]">
                <Info size={16} className="text-gray-400 shrink-0 mt-0.5" />
                <p className="text-[13px] text-gray-500 leading-relaxed font-medium">完成本次训练只会新增证据，不会凭一组题直接改变能力结论。</p>
             </div>
          </div>
        </div>
      </div>

      {/* Bottom Action Area */}
      <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
        <button onClick={() => onGo("practice")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform flex justify-center items-center gap-2 mb-4">
          开始这项训练 <ArrowRight size={18} />
        </button>
        <div className="flex justify-between items-center px-4">
           <button onClick={() => setDrawerOpen(true)} className="text-[14px] font-bold text-gray-600 active:scale-95 transition-transform flex items-center gap-1.5">
             <Settings size={14} className="text-gray-400" /> 调整这项任务
           </button>
           <div className="w-px h-3 bg-gray-300" />
           <button onClick={() => onGo("coach")} className="text-[14px] font-bold text-indigo-500 flex items-center gap-1.5 active:scale-95 transition-transform">
             带着这项安排问岸岸 <Sparkles size={14} />
           </button>
        </div>
      </div>

      {/* Adjustment Drawer */}
      <AnimatePresence>
        {drawerOpen && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-0 z-50 bg-gray-900/30 backdrop-blur-sm flex flex-col justify-end"
            onClick={() => setDrawerOpen(false)}
          >
            <motion.div 
              initial={{ y: "100%" }}
              animate={{ y: 0 }}
              exit={{ y: "100%" }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="bg-white rounded-t-[32px] p-6 pt-3 pb-safe max-h-[85vh] overflow-y-auto no-scrollbar"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-10 h-1.5 bg-gray-200 rounded-full mx-auto mb-6" />
              <div className="flex justify-between items-center mb-6">
                <h3 className="text-[20px] font-bold text-gray-900 tracking-tight">今天怎么练更合适</h3>
                <button onClick={() => setDrawerOpen(false)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center active:scale-95">
                  <X size={16} className="text-gray-500" />
                </button>
              </div>
              
              <div className="space-y-3 mb-6">
                 {/* Option 1 */}
                 <button className="w-full bg-[#F7F9FC] rounded-[24px] p-5 text-left border-[1.5px] border-gray-900 active:scale-[0.98] transition-transform">
                   <div className="flex justify-between items-center mb-2">
                      <span className="font-bold text-[16px] text-gray-900">原计划 · 30分钟</span>
                      <CheckCircle2 size={20} className="text-gray-900 fill-gray-900/10" />
                   </div>
                   <p className="text-[13px] text-gray-600 font-medium leading-relaxed">按当前15题执行，重点在时间控制和截位应用。</p>
                 </button>
                 
                 {/* Option 2 */}
                 <button className="w-full bg-white rounded-[24px] p-5 text-left border border-gray-100 shadow-[0_2px_10px_rgba(0,0,0,0.02)] active:scale-[0.98] transition-transform hover:bg-gray-50">
                   <div className="flex justify-between items-center mb-2">
                      <span className="font-bold text-[16px] text-gray-900">基础版 · 20分钟</span>
                      <span className="text-[11px] font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-md">耗时少</span>
                   </div>
                   <p className="text-[13px] text-gray-500 font-medium leading-relaxed">减少题量，保留完整复盘。适合精力不足时。</p>
                 </button>

                 {/* Option 3 */}
                 <button className="w-full bg-white rounded-[24px] p-5 text-left border border-gray-100 shadow-[0_2px_10px_rgba(0,0,0,0.02)] active:scale-[0.98] transition-transform hover:bg-gray-50">
                   <div className="flex justify-between items-center mb-2">
                      <span className="font-bold text-[16px] text-gray-900">强化版 · 60分钟</span>
                   </div>
                   <p className="text-[13px] text-gray-500 font-medium leading-relaxed">增加同类题与第二轮验证。会占用申论复习时间。</p>
                 </button>
              </div>

              <div className="h-px bg-gray-100 mb-2" />
              
              <div className="flex flex-col">
                 <button className="w-full py-4 text-left text-[15px] font-bold text-gray-700 flex items-center justify-between active:scale-[0.98] transition-transform px-2 hover:bg-gray-50 rounded-[16px]">
                    换一个薄弱点 <ChevronRight size={18} className="text-gray-400" />
                 </button>
                 <button className="w-full py-4 text-left text-[15px] font-bold text-gray-700 flex items-center justify-between active:scale-[0.98] transition-transform px-2 hover:bg-gray-50 rounded-[16px]">
                    移到明天 <ChevronRight size={18} className="text-gray-400" />
                 </button>
                 <button className="w-full py-4 text-left text-[15px] font-bold text-red-500 flex items-center justify-between active:scale-[0.98] transition-transform px-2 hover:bg-red-50 rounded-[16px]">
                    今天不练这个 <ChevronRight size={18} className="text-red-300" />
                 </button>
              </div>

              <button 
                onClick={() => setDrawerOpen(false)}
                className="w-full py-4 mt-6 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform"
              >
                确认调整
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
const WeeklyPlanPage = ({ onGo, onBack }: { onGo: (page: Page) => void; onBack: () => void }) => {
  const [expandedDay, setExpandedDay] = useState<number | null>(2); // Wednesday by default
  const [drawerState, setDrawerState] = useState<"none" | "edit_time" | "preview">("none");
  const [weekHours, setWeekHours] = useState([2, 2.5, 2.5, 2, 2, 3, 0.5]);

  const daysInfo = [
    { name: "周一", defaultStatus: "已完成", type: "past" },
    { name: "周二", defaultStatus: "已完成", type: "past" },
    { name: "周三", defaultStatus: "今天", type: "today" },
    { name: "周四", defaultStatus: "待执行", type: "future" },
    { name: "周五", defaultStatus: "待执行", type: "future" },
    { name: "周六", defaultStatus: "复盘", type: "review" },
    { name: "周日", defaultStatus: "机动", type: "flexible" },
  ];

  const days = [
    { day: "周一", status: "已完成", type: "past", time: "2h", count: 2, focus: "资料分析基础" },
    { day: "周二", status: "已完成", type: "past", time: "2.5h", count: 3, focus: "言语理解错题" },
    { day: "周三", status: "今天", type: "today", time: "2.5h", count: 3, focus: "比重估算验证" },
    { day: "周四", status: "待执行", type: "future", time: "2h", count: 2, focus: "言语理解专练" },
    { day: "周五", status: "待执行", type: "future", time: "2h", count: 2, focus: "判断推理强化" },
    { day: "周六", status: "复盘", type: "review", time: "3h", count: 1, focus: "全周错题重测" },
    { day: "周日", status: "机动", type: "flexible", time: "0.5h", count: 0, focus: "自由安排" },
  ];

  const handleUpdateHour = (i: number, delta: number) => {
    const newHours = [...weekHours];
    newHours[i] = Math.max(0, newHours[i] + delta);
    setWeekHours(newHours);
  };

  const applyPreset = (type: "steady" | "fulltime" | "sprint") => {
    if (type === "steady") setWeekHours([2, 2, 2, 2, 2, 4, 1]);
    if (type === "fulltime") setWeekHours([6, 6, 6, 6, 6, 4, 2]);
    if (type === "sprint") setWeekHours([8, 8, 8, 8, 8, 8, 4]);
  };

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative">
      <StatusBar />
      <WechatCapsule />
      <div className="absolute top-[48px] left-4 z-50">
        <button onClick={onBack} className="w-8 h-8 rounded-full bg-white/50 backdrop-blur-md border border-gray-200/50 flex items-center justify-center active:scale-95 shadow-sm">
          <ChevronLeft size={20} className="text-gray-900" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 pt-[104px] pb-32 flex flex-col gap-8 no-scrollbar">
        {/* Header Section */}
        <div>
          <div className="flex items-center gap-2 mb-4">
            <span className="text-[12px] font-bold text-indigo-600 bg-indigo-50/80 px-2.5 py-1 rounded-md">本周第3天</span>
            <span className="text-[12px] font-medium text-gray-500 bg-gray-200/50 px-2.5 py-1 rounded-md">已完成4.5小时</span>
            <span className="text-[12px] font-medium text-gray-500 bg-gray-200/50 px-2.5 py-1 rounded-md">计划14.5小时</span>
          </div>
          <h1 className="text-[26px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-3">这周，只追三个能验证的变化</h1>
          <p className="text-[15px] text-gray-500 leading-relaxed font-medium">5天训练＋1天复盘＋1天机动。计划会跟着你的真实时间变化。</p>
        </div>

        {/* Main Target Card */}
        <div>
          <div className="flex items-center gap-2 mb-4 ml-1">
             <Target size={18} className="text-gray-900" />
             <h3 className="text-[18px] font-bold text-gray-900 tracking-tight">本周三个目标</h3>
          </div>
          <div className="bg-white rounded-[32px] p-2 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex flex-col">
             
             {/* Row 1 */}
             <div className="p-4 flex flex-col gap-2 relative">
                <div className="flex items-center justify-between">
                   <div className="flex items-center gap-2">
                     <div className="w-6 h-6 rounded-full bg-indigo-50 flex items-center justify-center">
                       <span className="text-[12px] font-bold text-indigo-500">1</span>
                     </div>
                     <span className="text-[15px] font-bold text-gray-900">资料分析</span>
                   </div>
                   <span className="text-[11px] font-bold text-indigo-500 bg-indigo-50/80 px-2 py-0.5 rounded-md flex items-center gap-1">
                     <Sparkles size={10} />
                     正在验证
                   </span>
                </div>
                <p className="text-[14px] text-gray-600 font-medium ml-8 leading-relaxed">
                   可估算题平均用时接近60秒
                </p>
             </div>

             <div className="h-px bg-gray-100/60 mx-4" />

             {/* Row 2 */}
             <div className="p-4 flex flex-col gap-2 relative">
                <div className="flex items-center justify-between">
                   <div className="flex items-center gap-2">
                     <div className="w-6 h-6 rounded-full bg-gray-50 flex items-center justify-center">
                       <span className="text-[12px] font-bold text-gray-500">2</span>
                     </div>
                     <span className="text-[15px] font-bold text-gray-900">言语理解</span>
                   </div>
                   <span className="text-[11px] font-bold text-gray-500 bg-gray-100/80 px-2 py-0.5 rounded-md">
                     证据积累中
                   </span>
                </div>
                <p className="text-[14px] text-gray-600 font-medium ml-8 leading-relaxed">
                   减少“高信心但答错”的题
                </p>
             </div>

             <div className="h-px bg-gray-100/60 mx-4" />

             {/* Row 3 */}
             <div className="p-4 flex flex-col gap-2 relative">
                <div className="flex items-center justify-between">
                   <div className="flex items-center gap-2">
                     <div className="w-6 h-6 rounded-full bg-gray-50 flex items-center justify-center">
                       <span className="text-[12px] font-bold text-gray-500">3</span>
                     </div>
                     <span className="text-[15px] font-bold text-gray-900">判断推理</span>
                   </div>
                   <span className="text-[11px] font-bold text-gray-400 bg-gray-50 px-2 py-0.5 rounded-md">
                     尚未验证
                   </span>
                </div>
                <p className="text-[14px] text-gray-500 font-medium ml-8 leading-relaxed">
                   图形推理连续两次复测稳定
                </p>
             </div>

          </div>
        </div>

        {/* Weekly Calendar */}
        <div>
          <div className="flex items-center gap-2 mb-4 ml-1">
             <CalendarDays size={18} className="text-gray-900" />
             <h3 className="text-[18px] font-bold text-gray-900 tracking-tight">本周安排</h3>
          </div>
          <div className="bg-white rounded-[32px] p-2 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex flex-col gap-1">
             {days.map((d, i) => (
                <div key={i} className="flex flex-col">
                  <button 
                    onClick={() => setExpandedDay(expandedDay === i ? null : i)}
                    className="flex items-center justify-between p-3 px-4 rounded-[20px] active:scale-[0.98] transition-all"
                    style={{ backgroundColor: expandedDay === i ? "#F7F9FC" : "transparent" }}
                  >
                     <div className="flex items-center gap-3">
                       <span className={`text-[14px] font-bold ${d.type === "today" ? "text-indigo-600" : "text-gray-900"}`}>{d.day}</span>
                       <span className={`text-[12px] font-medium px-2 py-0.5 rounded-md ${
                          d.type === "past" ? "text-gray-400 bg-gray-50" :
                          d.type === "today" ? "text-indigo-500 bg-indigo-50" :
                          d.type === "review" ? "text-orange-500 bg-orange-50" :
                          "text-gray-500 bg-gray-50"
                       }`}>
                         {d.status}
                       </span>
                     </div>
                     <ChevronDown size={14} className={`text-gray-400 transition-transform ${expandedDay === i ? "rotate-180" : ""}`} />
                  </button>
                  <AnimatePresence>
                     {expandedDay === i && (
                        <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                           <div className="px-4 pb-4 pt-1 flex gap-4 text-[13px]">
                             <div className="flex flex-col gap-1">
                               <span className="text-gray-400 font-medium">可用时间</span>
                               <span className="text-gray-900 font-bold">{d.time}</span>
                             </div>
                             <div className="flex flex-col gap-1">
                               <span className="text-gray-400 font-medium">任务数量</span>
                               <span className="text-gray-900 font-bold">{d.count} 项</span>
                             </div>
                             <div className="flex flex-col gap-1 flex-1">
                               <span className="text-gray-400 font-medium">训练重点</span>
                               <span className="text-gray-900 font-bold truncate">{d.focus}</span>
                             </div>
                           </div>
                        </motion.div>
                     )}
                  </AnimatePresence>
                </div>
             ))}
          </div>
        </div>

        {/* Info Box */}
        <div className="bg-indigo-50/40 rounded-[24px] p-5 border border-indigo-50 flex flex-col gap-3">
           <div className="flex gap-2 items-start">
             <Sparkles size={16} className="text-indigo-500 shrink-0 mt-0.5" />
             <div>
               <h4 className="text-[14px] font-bold text-gray-900 mb-1">岸岸建议调整</h4>
               <p className="text-[13px] text-gray-600 font-medium leading-relaxed">
                 周一和周二的学习时间少于计划。岸岸建议把判断推理复测移到周六。
               </p>
             </div>
           </div>
           <div className="flex gap-3 mt-1">
             <button className="flex-1 py-2.5 bg-indigo-500 text-white rounded-[12px] font-bold text-[13px] active:scale-95 transition-transform">
               查看变化
             </button>
             <button className="flex-1 py-2.5 bg-white border border-indigo-100 text-indigo-600 rounded-[12px] font-bold text-[13px] active:scale-95 transition-transform">
               暂不调整
             </button>
           </div>
        </div>

      </div>

      {/* Bottom Action Area */}
      <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
        <button onClick={() => setDrawerState("edit_time")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform flex justify-center items-center gap-2 mb-4">
          调整本周时间
        </button>
        <div className="flex justify-center items-center px-4">
           <button onClick={() => onGo("coach")} className="text-[14px] font-bold text-indigo-500 flex items-center gap-1.5 active:scale-95 transition-transform">
             带着本周计划问岸岸 <Sparkles size={14} />
           </button>
        </div>
      </div>

      {/* Drawers */}
      <AnimatePresence>
        {(drawerState === "edit_time" || drawerState === "preview") && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-0 z-50 bg-gray-900/30 backdrop-blur-sm flex flex-col justify-end"
            onClick={() => setDrawerState("none")}
          >
            <motion.div 
              initial={{ y: "100%" }}
              animate={{ y: 0 }}
              exit={{ y: "100%" }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="bg-white rounded-t-[32px] p-6 pt-3 pb-safe max-h-[85vh] overflow-y-auto no-scrollbar flex flex-col"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-10 h-1.5 bg-gray-200 rounded-full mx-auto mb-6 shrink-0" />
              
              {drawerState === "edit_time" && (
                <>
                  <div className="flex justify-between items-center mb-6 shrink-0">
                    <h3 className="text-[20px] font-bold text-gray-900 tracking-tight">这周能学多久</h3>
                    <button onClick={() => setDrawerState("none")} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center active:scale-95">
                      <X size={16} className="text-gray-500" />
                    </button>
                  </div>
                  
                  <div className="flex gap-2 mb-6 shrink-0">
                     <button onClick={() => applyPreset("steady")} className="flex-1 py-2 bg-indigo-50 text-indigo-600 rounded-[12px] font-bold text-[13px] active:scale-95 transition-transform border border-indigo-100/50">
                       在职稳态
                     </button>
                     <button onClick={() => applyPreset("fulltime")} className="flex-1 py-2 bg-indigo-50 text-indigo-600 rounded-[12px] font-bold text-[13px] active:scale-95 transition-transform border border-indigo-100/50">
                       全职备考
                     </button>
                     <button onClick={() => applyPreset("sprint")} className="flex-1 py-2 bg-indigo-50 text-indigo-600 rounded-[12px] font-bold text-[13px] active:scale-95 transition-transform border border-indigo-100/50">
                       全力冲刺
                     </button>
                  </div>

                  <div className="flex flex-col gap-3 overflow-y-auto mb-6 no-scrollbar">
                     {daysInfo.map((d, i) => (
                       <div key={i} className="flex items-center justify-between p-4 bg-[#F7F9FC] rounded-[20px] border border-gray-100/50">
                         <div className="flex items-center gap-3">
                           <span className={`text-[15px] font-bold ${d.type === "today" ? "text-indigo-600" : "text-gray-900"}`}>{d.name}</span>
                           <span className="text-[11px] font-medium text-gray-400 bg-gray-200/40 px-2 py-0.5 rounded-md">{d.defaultStatus}</span>
                         </div>
                         <div className="flex items-center gap-4">
                           <button onClick={() => handleUpdateHour(i, -0.5)} className="w-8 h-8 rounded-full bg-white shadow-sm flex items-center justify-center text-gray-500 active:scale-95 border border-gray-100">
                             <MinusCircle size={16} />
                           </button>
                           <span className="text-[16px] font-bold text-gray-900 w-10 text-center">{weekHours[i].toFixed(1)} h</span>
                           <button onClick={() => handleUpdateHour(i, 0.5)} className="w-8 h-8 rounded-full bg-white shadow-sm flex items-center justify-center text-gray-900 active:scale-95 border border-gray-100">
                             <PlusCircle size={16} />
                           </button>
                         </div>
                       </div>
                     ))}
                  </div>

                  <button 
                    onClick={() => setDrawerState("preview")}
                    className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform shrink-0"
                  >
                    预览调整
                  </button>
                </>
              )}

              {drawerState === "preview" && (
                <>
                  <div className="flex justify-between items-center mb-6 shrink-0">
                    <h3 className="text-[20px] font-bold text-gray-900 tracking-tight">计划变化预览</h3>
                    <button onClick={() => setDrawerState("none")} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center active:scale-95">
                      <X size={16} className="text-gray-500" />
                    </button>
                  </div>

                  <div className="flex flex-col gap-6 overflow-y-auto no-scrollbar pb-6">
                    <div className="bg-indigo-50/50 rounded-[24px] p-5 border border-indigo-50">
                       <div className="flex gap-2 items-center mb-3">
                         <Sparkles size={16} className="text-indigo-500" />
                         <span className="text-[15px] font-bold text-gray-900">岸岸建议</span>
                       </div>
                       <p className="text-[13px] text-gray-600 font-medium leading-relaxed mb-3">
                         时间整体缩减，将重点保住本周的核心目标验证。建议删除两组非核心基础练习，复测集中移到周六。
                       </p>
                       <div className="flex items-center gap-2">
                         <CheckCircle2 size={14} className="text-green-500" />
                         <span className="text-[12px] font-bold text-gray-700">本次调整不影响本周三个目标</span>
                       </div>
                    </div>

                    <div className="flex flex-col gap-4">
                      <h4 className="text-[15px] font-bold text-gray-900 ml-1">任务变动</h4>
                      <div className="flex flex-col gap-3">
                         <div className="flex items-start gap-3 p-4 bg-[#F7F9FC] rounded-[20px]">
                            <div className="bg-orange-100 text-orange-600 text-[10px] font-bold px-2 py-0.5 rounded mt-0.5 shrink-0">移到周六</div>
                            <div>
                               <p className="text-[14px] font-bold text-gray-900 mb-1">判断推理复测</p>
                               <p className="text-[12px] text-gray-500 font-medium">从周五移至周六，保证复测时间连贯</p>
                            </div>
                         </div>
                         <div className="flex items-start gap-3 p-4 bg-[#F7F9FC] rounded-[20px]">
                            <div className="bg-gray-200 text-gray-500 text-[10px] font-bold px-2 py-0.5 rounded mt-0.5 shrink-0">建议删除</div>
                            <div>
                               <p className="text-[14px] font-bold text-gray-900 mb-1">言语理解 · 词语积累</p>
                               <p className="text-[12px] text-gray-500 font-medium">当前能力较稳，时间紧凑时可略过</p>
                            </div>
                         </div>
                         <div className="flex items-start gap-3 p-4 bg-[#F7F9FC] rounded-[20px]">
                            <div className="bg-indigo-100 text-indigo-600 text-[10px] font-bold px-2 py-0.5 rounded mt-0.5 shrink-0">保留优先</div>
                            <div>
                               <p className="text-[14px] font-bold text-gray-900 mb-1">资料分析 · 截位估算</p>
                               <p className="text-[12px] text-gray-500 font-medium">本周首要目标，不可精简</p>
                            </div>
                         </div>
                      </div>
                    </div>

                    <div className="flex flex-col gap-4">
                      <h4 className="text-[15px] font-bold text-gray-900 ml-1">时长对比</h4>
                      <div className="bg-[#F7F9FC] rounded-[20px] p-4 flex flex-col gap-2">
                         {daysInfo.map((d, i) => (
                           <div key={i} className="flex items-center justify-between py-1">
                             <span className="text-[13px] font-medium text-gray-500">{d.name}</span>
                             <div className="flex items-center gap-3">
                               <span className="text-[13px] font-medium text-gray-400 line-through">2.5h</span>
                               <ArrowRight size={12} className="text-gray-300" />
                               <span className="text-[13px] font-bold text-gray-900">{weekHours[i].toFixed(1)}h</span>
                             </div>
                           </div>
                         ))}
                      </div>
                    </div>
                  </div>

                  <div className="flex gap-3 shrink-0 pt-2 border-t border-gray-50">
                    <button 
                      onClick={() => setDrawerState("edit_time")}
                      className="flex-1 py-4 bg-[#F7F9FC] text-gray-700 rounded-[20px] font-bold text-[15px] active:scale-[0.98] transition-transform border border-gray-100"
                    >
                      返回修改
                    </button>
                    <button 
                      onClick={() => setDrawerState("none")}
                      className="flex-1 py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] shadow-[0_4px_15px_rgba(0,0,0,0.1)] active:scale-[0.98] transition-transform"
                    >
                      确认调整
                    </button>
                  </div>
                </>
              )}

            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
const TodayPage = ({ onGo, pendingReview }: { onGo: (page: Page) => void, pendingReview?: boolean }) => {
  const [isStarted, setIsStarted] = useState(false);
  const [insightExpanded, setInsightExpanded] = useState(false);
  const [timeDrawerOpen, setTimeDrawerOpen] = useState(false);
  
  const [studyTime, setStudyTime] = useState(2.5);
  const [energy, setEnergy] = useState("良好");
  const [stress, setStress] = useState("一般");

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative">
      <StatusBar />
      <WechatCapsule />
      
      <div className="flex-1 overflow-y-auto px-5 pt-[52px] pb-8 flex flex-col gap-5 no-scrollbar relative z-0">
        {/* Top Greeting */}
        <div className="flex justify-between items-end">
          <div>
            <p className="text-[14px] text-gray-500 font-medium mb-0.5">10月24日 星期二</p>
            <h2 className="text-[26px] font-bold text-gray-900 tracking-tight">早上好，林小鱼</h2>
            <p className="text-[14px] text-gray-500 mt-1 font-medium">距离 202X 国考还有 <span className="text-gray-900 font-bold">42</span> 天</p>
          </div>
          <button onClick={() => onGo("photo")} className="w-10 h-10 rounded-full bg-white shadow-[0_4px_20px_rgba(0,0,0,0.04)] flex items-center justify-center shrink-0 active:scale-95 transition-transform mb-1">
            <Camera size={18} className="text-gray-900" />
          </button>
        </div>

        {/* Daily Opening Card (Highly Compact) */}
        <AnimatePresence mode="wait">
          {!isStarted ? (
            <motion.div 
              key="expanded"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, height: 0, overflow: "hidden", marginBottom: 0 }}
              className="bg-white rounded-[24px] p-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] shrink-0"
            >
              <div className="flex items-center gap-2 mb-2">
                <CloudRain size={14} className="text-gray-400 shrink-0" />
                <span className="text-[13px] text-gray-700 font-medium">北京 28℃ 下午有雨</span>
              </div>
              <div className="flex items-center gap-2 mb-4">
                <Target size={14} className="text-gray-400 shrink-0" />
                <span className="text-[13px] text-gray-700 font-medium">今天安排 2.5 小时，先处理资料分析</span>
              </div>
              
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Coffee size={14} className="text-gray-400" />
                  <span className="text-[12px] text-gray-500">顺便看咖啡？</span>
                  <div className="flex gap-1.5 ml-1">
                    <button className="px-2 py-1 bg-gray-50 rounded-md text-[11px] font-medium text-gray-700 active:scale-95">帮我看看</button>
                    <button className="px-2 py-1 bg-transparent rounded-md text-[11px] font-medium text-gray-400 active:scale-95">今天不喝</button>
                  </div>
                </div>
                <button 
                  onClick={() => setIsStarted(true)} 
                  className="px-4 py-2 bg-gray-900 text-white rounded-full font-bold text-[13px] active:scale-[0.98] transition-transform"
                >
                  开始学习
                </button>
              </div>
            </motion.div>
          ) : (
            <motion.div 
              key="collapsed"
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="bg-white rounded-full px-5 py-3 shadow-[0_4px_20px_rgba(0,0,0,0.02)] flex items-center justify-between shrink-0"
            >
              <div className="flex items-center gap-2">
                <Target size={14} className="text-gray-900" />
                <span className="text-[13px] text-gray-900 font-bold">学习进行中 · 2.5小时</span>
              </div>
              <span className="text-[12px] text-gray-400 font-medium">下午有雨 28℃</span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Tasks Section Header with Time Summary */}
        <div className="flex items-end justify-between mt-2 shrink-0">
          <div>
            <h3 className="font-bold text-[20px] text-gray-900 tracking-tight">今日任务</h3>
            <div className="flex items-center gap-2 mt-1.5">
              <span className="text-[12px] font-medium text-gray-500">
                今天可学 {studyTime}h · 精力{energy} · 压力{stress}
              </span>
              <button onClick={() => onGo("today_adjustment")} className="text-[12px] font-bold text-indigo-500 px-3 py-1 bg-indigo-50 rounded-full active:scale-95 transition-transform">调整今天</button>
            </div>
          </div>
        </div>

        
        {pendingReview && (
          <div className="shrink-0 bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.08)] flex flex-col gap-4 border border-orange-100/80 mb-4">
            <div className="flex justify-between items-start">
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="bg-orange-50 text-orange-600 text-[11px] font-bold px-2.5 py-1 rounded-md">待复盘</span>
                </div>
                <h4 className="font-bold text-gray-900 text-[18px]">资料分析 · 2 道错题</h4>
              </div>
            </div>
            <button onClick={() => onGo("result_summary")} className="w-full py-3.5 mt-1 bg-gray-900 text-white rounded-[20px] font-semibold text-[15px] active:scale-[0.98] transition-transform shadow-[0_4px_15px_rgba(0,0,0,0.1)]">
              继续复盘
            </button>
          </div>
        )}
{/* Task 1: Main Focus (Merged with An'an Insight) */}
        <div className="shrink-0 bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.06)] relative overflow-hidden group border border-indigo-50/50">
          <div className="absolute inset-0 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 opacity-[0.02]" />
          
          {/* AI Insight Section */}
          <div className="relative z-10 flex items-start gap-3 mb-5 border-b border-gray-100/60 pb-5">
            <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shrink-0 shadow-sm shadow-purple-500/20">
              <Sparkles size={16} className="text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="font-bold text-gray-900 text-[14px] mb-1">岸岸建议</h3>
              <p className="text-[13px] text-gray-700 leading-relaxed">
                你最近在资料分析的比重题上连续失分，今天先用一组短题确认问题出在列式还是计算。
              </p>
              <AnimatePresence>
                {insightExpanded && (
                  <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                    <p className="text-[12px] text-gray-500 mt-2 leading-relaxed bg-gray-50 p-2.5 rounded-xl">
                      昨天完成的《行测模考（三）》中，4道比重计算题耗时均超过90秒且只有1道正确。这说明你可能在直接计算大数字，而不是先进行截位估算。
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>
              <button onClick={() => setInsightExpanded(!insightExpanded)} className="text-[11px] font-medium text-gray-400 mt-1.5 flex items-center gap-0.5 active:scale-95">
                {insightExpanded ? "收起" : "为什么今天先做这个？"} <ChevronDown size={12} className={insightExpanded ? "rotate-180" : ""} />
              </button>
            </div>
          </div>

          {/* Task Action Section */}
          <div className="relative z-10">
            <div className="flex justify-between items-start mb-4">
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="bg-indigo-50 text-indigo-600 text-[11px] font-bold px-2 py-0.5 rounded-md">错题复盘</span>
                  <span className="text-gray-400 text-[12px] font-medium">· 预计 25 分钟</span>
                </div>
                <h4 className="font-bold text-gray-900 text-[18px]">资料分析 · 比重估算</h4>
              </div>
              <button onClick={() => onGo("task_basis")} className="text-[12px] font-bold text-indigo-500 bg-indigo-50 px-2.5 py-1.5 rounded-full active:scale-95 transition-transform flex items-center gap-0.5 mt-2">
                为什么这样练 <ChevronRight size={12} />
              </button>
            </div>
            <button onClick={() => onGo("practice")} className="w-full py-3.5 bg-gray-900 text-white rounded-[20px] font-semibold text-[15px] active:scale-[0.98] transition-transform shadow-[0_4px_15px_rgba(0,0,0,0.1)] flex items-center justify-center gap-1.5">
              <Play size={16} className="fill-white" /> <span>开始第一项任务</span>
            </button>
          </div>
        </div>

        {/* Other Tasks List */}
        <div className="shrink-0 flex flex-col gap-3">
          {/* Task 2: Standard (Compact) */}
          <div className="bg-white rounded-[24px] px-5 py-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center shrink-0">
                <FileText size={14} className="text-gray-500" />
              </div>
              <div>
                <h4 className="font-bold text-gray-900 text-[15px] mb-0.5">提出对策题练习</h4>
                <span className="text-gray-400 text-[11px] font-medium">申论训练 · 预计 30 分钟</span>
              </div>
            </div>
            <button onClick={() => onGo("essay")} className="px-3.5 py-1.5 bg-[#F7F9FC] text-gray-900 rounded-full font-semibold text-[12px] active:scale-95 transition-transform">
              继续
            </button>
          </div>
          
          {/* Task 3: Completed (Extremely Weakened) */}
          <div className="flex items-center gap-3 px-5 py-2 opacity-50">
            <div className="w-8 h-8 flex items-center justify-center shrink-0">
              <CheckCircle2 size={18} className="text-gray-400" />
            </div>
            <div className="flex-1">
              <h4 className="font-bold text-gray-500 text-[14px] line-through decoration-gray-300">政治常识专项</h4>
            </div>
            <button className="text-[11px] font-medium text-gray-400 active:scale-95">
              查看复盘
            </button>
          </div>
        </div>

        {/* Secondary Tools (Light Row) */}
        <div className="shrink-0 mt-3 mb-2">
          <div className="flex justify-center mb-4">
            <button onClick={() => onGo("daily_settlement")} className="w-full py-3.5 bg-gray-50 border border-gray-100 text-gray-600 rounded-[20px] font-bold text-[14px] active:scale-95 transition-transform flex items-center justify-center gap-2">
              <CheckCircle2 size={16} />
              <span>结束今天并复盘</span>
            </button>
          </div>
          <div className="flex items-center gap-6 justify-center">
            {[
              { icon: BookOpen, label: "错题与掌握", page: "practice" as Page },
              { icon: CalendarDays, label: "周计划", page: "weekly_plan" as Page },
              { icon: RefreshCw, label: "每日结算", page: "daily_settlement" as Page },
            ].map((tool, i) => (
              <button key={i} onClick={() => onGo(tool.page)} className="flex items-center gap-1.5 active:scale-95 transition-transform opacity-70 hover:opacity-100">
                <tool.icon size={14} className="text-gray-500" />
                <span className="text-[12px] font-medium text-gray-600">{tool.label}</span>
              </button>
            ))}
          </div>
        </div>
        
      </div>

      {/* Time Drawer */}
      <AnimatePresence>
        {timeDrawerOpen && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-0 z-50 bg-gray-900/20 backdrop-blur-sm flex flex-col justify-end"
            onClick={() => setTimeDrawerOpen(false)}
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
                <h3 className="text-[20px] font-bold text-gray-900">调整今日状态</h3>
                <button onClick={() => setTimeDrawerOpen(false)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center">
                  <X size={16} className="text-gray-500" />
                </button>
              </div>
              
              <div className="space-y-6">
                <div>
                  <p className="text-[14px] font-medium text-gray-700 mb-3">今天还能专注学习多久？</p>
                  <div className="flex items-center gap-4 bg-[#F7F9FC] rounded-[24px] p-4 border border-gray-100/50">
                    <Clock size={20} className="text-gray-400" />
                    <input 
                      type="range" 
                      min="0.5" max="10" step="0.5" 
                      value={studyTime} 
                      onChange={e => setStudyTime(Number(e.target.value))}
                      className="flex-1 accent-gray-900 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
                    />
                    <span className="font-bold text-[16px] text-gray-900 w-12 text-right">{studyTime} h</span>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <p className="text-[14px] font-medium text-gray-700 mb-3">精力状态</p>
                    <div className="flex flex-col gap-2">
                      {["充沛", "良好", "疲惫"].map(v => (
                        <button 
                          key={v}
                          onClick={() => setEnergy(v)}
                          className={`w-full py-3 rounded-[16px] text-[14px] font-medium transition-colors ${energy === v ? "bg-gray-900 text-white" : "bg-[#F7F9FC] text-gray-600 border border-gray-100/50"}`}
                        >
                          {v}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="text-[14px] font-medium text-gray-700 mb-3">心理压力</p>
                    <div className="flex flex-col gap-2">
                      {["轻松", "一般", "紧绷"].map(v => (
                        <button 
                          key={v}
                          onClick={() => setStress(v)}
                          className={`w-full py-3 rounded-[16px] text-[14px] font-medium transition-colors ${stress === v ? "bg-gray-900 text-white" : "bg-[#F7F9FC] text-gray-600 border border-gray-100/50"}`}
                        >
                          {v}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              <button 
                onClick={() => setTimeDrawerOpen(false)}
                className="w-full py-4 mt-8 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] active:scale-[0.98] transition-transform shadow-[0_4px_20px_rgba(0,0,0,0.1)]"
              >
                按这个状态重新安排
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

// ═══════════════ ABILITY TAB (Replaces ReportPage) ══════════════════════════
const radarDataXingce = [
  { subject: "政治理论", score: 80, full: 100 },
  { subject: "常识判断", score: 58, full: 100 },
  { subject: "言语理解", score: 74, full: 100 },
  { subject: "数量关系", score: 42, full: 100 },
  { subject: "判断推理", score: 68, full: 100 },
  { subject: "资料分析", score: 71, full: 100 },
];

const xingceModules = [
  { name: "政治理论", metric: "正确率 82%", status: "较可靠" },
  { name: "常识判断", metric: "正确率 55%", status: "需要更新" },
  { name: "言语理解与表达", metric: "平均用时 48s", status: "较可靠" },
  { name: "数量关系", metric: "稳定掌握 2个", status: "正在形成" },
  { name: "判断推理", metric: "正确率 68%", status: "较可靠" },
  { name: "资料分析", metric: "平均用时 75s", status: "较可靠" },
];

const shenlunModules = [
  { name: "材料理解", count: 12, status: "较可靠", verify: false },
  { name: "要点提炼", count: 8, status: "正在形成", verify: true },
  { name: "结构组织", count: 5, status: "需要更新", verify: false },
  { name: "表达准确", count: 10, status: "较可靠", verify: false },
  { name: "对策可行", count: 3, status: "正在形成", verify: true },
  { name: "迁移能力", count: 0, status: "还需完成真实作答", verify: false },
];

const errorsData = [
  { desc: "比重题直接计算大数，忽略截位估算", count: 4, contrary: false, next: "明日复测" },
  { desc: "逻辑填空过早锁定近义词", count: 3, contrary: true, next: "待复测" },
  { desc: "申论对策缺少执行主体", count: 2, contrary: false, next: "周五复测" },
];

const AbilityTab = ({ onGo }: { onGo: (page: Page) => void }) => {
  const [subjectTab, setSubjectTab] = useState<"xingce" | "shenlun">("xingce");

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC]">
      <StatusBar />
      <WechatCapsule />

      <div className="flex-1 overflow-y-auto px-5 pt-[52px] pb-8 flex flex-col gap-6 no-scrollbar relative z-0">
        
        {/* Header */}
        <div className="flex justify-between items-start">
          <div>
            <h2 className="text-[28px] font-bold text-gray-900 tracking-tight">能力</h2>
            <p className="text-[14px] text-gray-500 mt-1 font-medium">知道现在在哪，才知道下一步往哪走</p>
            <p className="text-[11px] text-gray-400 mt-3 font-medium">今天更新 · 已参考 156 条学习记录</p>
          </div>
        </div>

        {/* Goal & Gap */}
        <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] shrink-0">
           <div className="flex justify-between items-center mb-4">
             <div className="flex items-center gap-2">
               <h3 className="font-bold text-[16px] text-gray-900">目标对照</h3>
               <span className="text-[13px] text-gray-500 font-medium truncate max-w-[120px]">· 国家统计局 研究室文秘</span>
             </div>
             <button onClick={() => onGo("position_workbench")} className="text-[12px] text-gray-400 font-medium flex items-center gap-0.5 active:scale-95 transition-transform">
               查看怎么算的 <ChevronRight size={12} />
             </button>
           </div>
           <div className="flex items-center gap-2">
             <div className="flex-1 bg-[#F7F9FC] rounded-[16px] py-3 px-1 text-center">
               <p className="text-[11px] text-gray-500 font-medium mb-1 whitespace-nowrap">能力估计</p>
               <p className="text-[15px] font-bold text-gray-900 tracking-tighter whitespace-nowrap">128–132</p>
             </div>
             <div className="flex-1 bg-[#F7F9FC] rounded-[16px] py-3 px-1 text-center">
               <p className="text-[11px] text-gray-500 font-medium mb-1 whitespace-nowrap">岗位备考参考线</p>
               <p className="text-[15px] font-bold text-gray-900 tracking-tighter whitespace-nowrap">138.5</p>
             </div>
             <div className="flex-1 bg-[#F7F9FC] rounded-[16px] py-3 px-1 text-center">
               <p className="text-[11px] text-gray-500 font-medium mb-1 whitespace-nowrap">当前差距</p>
               <p className="text-[15px] font-bold text-gray-900 tracking-tighter whitespace-nowrap">6.5–10.5 分</p>
             </div>
           </div>
           <p className="text-[10px] text-gray-400 mt-4 text-center">用于备考定位，不代表录取结果。</p>
        </div>

        {/* Ability Overview */}
        <div className="shrink-0 bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="font-bold text-[16px] text-gray-900">当前能力总览</h3>
            <span className="text-[11px] font-medium text-indigo-500 bg-indigo-50 px-2 py-0.5 rounded-md">较可靠</span>
          </div>
          <div className="flex items-end gap-6 mt-1">
            <div className="relative">
              <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 blur-xl opacity-15" />
              <div className="relative">
                <p className="text-[12px] text-gray-400 font-medium mb-0.5">综合区间</p>
                <p className="text-[28px] font-bold text-transparent bg-clip-text bg-gradient-to-r from-indigo-600 to-purple-600 tracking-tight">128–132</p>
              </div>
            </div>
            <div className="flex gap-5 pb-1.5">
              <div>
                <p className="text-[12px] text-gray-400 font-medium mb-0.5">行测</p>
                <p className="text-[16px] font-bold text-gray-700">64–67</p>
              </div>
              <div>
                <p className="text-[12px] text-gray-400 font-medium mb-0.5">申论</p>
                <p className="text-[16px] font-bold text-gray-700">64–65</p>
              </div>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 p-1 bg-gray-100/50 rounded-[16px] shrink-0 w-[160px] mx-auto mt-2">
          <button onClick={() => setSubjectTab("xingce")} className={`flex-1 py-1.5 rounded-[12px] text-[13px] font-bold transition-all ${subjectTab === "xingce" ? "bg-white text-gray-900 shadow-[0_2px_8px_rgba(0,0,0,0.04)]" : "text-gray-500"}`}>行测</button>
          <button onClick={() => setSubjectTab("shenlun")} className={`flex-1 py-1.5 rounded-[12px] text-[13px] font-bold transition-all ${subjectTab === "shenlun" ? "bg-white text-gray-900 shadow-[0_2px_8px_rgba(0,0,0,0.04)]" : "text-gray-500"}`}>申论</button>
        </div>

        {/* Xingce View */}
        {subjectTab === "xingce" && (
          <div className="shrink-0 flex flex-col gap-6">
            <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)]">
              <div className="mb-4">
                <h4 className="text-[14px] font-bold text-gray-900 mb-0.5">行测模块状态</h4>
                <p className="text-[11px] text-gray-500 font-medium">综合正确率、用时、稳定掌握和复测表现，不等同于卷面分。</p>
              </div>
              <div style={{ height: 220 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <RadarChart key="radar-chart-xingce" data={radarDataXingce} margin={{ top: 10, right: 30, bottom: 10, left: 30 }} id="ability-radar-xingce">
                    <PolarGrid key="polar-grid-xingce" stroke="#F3F4F6" strokeWidth={1.5} />
                    <PolarAngleAxis key="polar-angle-xingce" dataKey="subject" tick={{ fontSize: 11, fill: "#6B7280", fontWeight: 600 }} />
                    <Radar key="radar-area-xingce" dataKey="score" stroke="#111827" fill="#111827" fillOpacity={0.08} strokeWidth={2} />
                  </RadarChart>
                </ResponsiveContainer>
              </div>
              <p className="text-[13px] text-gray-600 font-medium leading-relaxed mt-2 text-center px-2">
                资料分析速度稳定，但数量关系准确率和时间控制仍不稳定。
              </p>
            </div>

            <div className="flex flex-col gap-3">
              {xingceModules.map((m, i) => (
                <button key={i} onClick={() => onGo("placeholder")} className="bg-white rounded-[24px] px-5 py-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] flex items-center justify-between active:scale-95 transition-transform text-left">
                  <span className="font-bold text-[15px] text-gray-900">{m.name}</span>
                  <div className="flex items-center gap-3">
                    <span className="text-[13px] font-medium text-gray-600">{m.metric}</span>
                    <span className={`text-[11px] font-medium px-2 py-0.5 rounded-md ${m.status === "待测" ? "bg-gray-100 text-gray-400" : m.status === "需要更新" ? "bg-orange-50 text-orange-500" : "bg-indigo-50 text-indigo-500"}`}>{m.status}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Shenlun View */}
        {subjectTab === "shenlun" && (
          <div className="shrink-0 flex flex-col gap-3">
            {shenlunModules.map((m, i) => (
              <button key={i} onClick={() => onGo("placeholder")} className="bg-white rounded-[24px] px-5 py-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] flex items-center justify-between active:scale-95 transition-transform text-left">
                <div>
                  <h4 className="font-bold text-[15px] text-gray-900 mb-0.5">{m.name}</h4>
                  <span className="text-[11px] font-medium text-gray-400">已有效作答 {m.count} 次</span>
                </div>
                <div className="flex items-center gap-2">
                  {m.verify && <span className="text-[11px] font-medium bg-rose-50 text-rose-500 px-2 py-0.5 rounded-md">需再次验证</span>}
                  <span className={`text-[11px] font-medium px-2 py-0.5 rounded-md ${m.status === "还需完成真实作答" ? "bg-gray-100 text-gray-400" : m.status === "需要更新" ? "bg-orange-50 text-orange-500" : "bg-indigo-50 text-indigo-500"}`}>{m.status}</span>
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Recurring Errors */}
        <div className="shrink-0 mt-2">
          <h3 className="font-bold text-[18px] text-gray-900 tracking-tight mb-4 ml-1">最近反复出现的问题</h3>
          <div className="flex flex-col gap-3">
            {errorsData.map((e, i) => (
              <button key={i} onClick={() => onGo("placeholder")} className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] flex flex-col gap-3 text-left active:scale-[0.98] transition-transform">
                <p className="text-[14px] font-bold text-gray-900 leading-relaxed">{e.desc}</p>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-medium text-gray-500 bg-gray-50 px-2 py-0.5 rounded-md">出现 {e.count} 次</span>
                    {e.contrary && <span className="text-[11px] font-medium text-gray-400 bg-gray-50 px-2 py-0.5 rounded-md">有相反表现</span>}
                  </div>
                  <span className="text-[11px] font-medium text-indigo-500">{e.next}</span>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* AI Action Suggestion */}
        <div className="shrink-0 mt-4 bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] relative overflow-hidden border border-indigo-50/50">
          <div className="absolute inset-0 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 opacity-[0.02]" />
          <div className="relative z-10 flex flex-col gap-4">
            <div className="flex items-center gap-2">
              <Sparkles size={16} className="text-indigo-500" />
              <h3 className="font-bold text-[15px] text-gray-900">岸岸行动建议</h3>
            </div>
            <p className="text-[13px] text-gray-600 leading-relaxed">
              目前最值得优先修复的是数量关系的时间控制。需要岸岸结合今天的安排解释一下吗？
            </p>
            <button onClick={() => onGo("coach")} className="w-full py-3 bg-[#F7F9FC] text-indigo-600 rounded-full font-bold text-[13px] active:scale-95 transition-transform mt-1 border border-indigo-50">
              带着当前能力问岸岸
            </button>
          </div>
        </div>

      </div>
    </div>
  );
};

// ═══════════════ AI COACH TAB ════════════════════════════════════════════════
const CoachTab = ({ onGo }: { onGo: (page: Page) => void }) => {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [viewState, setViewState] = useState<"default" | "explanation" | "result">("default");

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
      <div className="relative z-10 flex flex-col h-full">
        <StatusBar />
        <WechatCapsule />
        <div className="px-5 pt-4 pb-2">
          <h2 className="text-[28px] font-bold text-gray-900 tracking-tight">岸岸</h2>
          <p className="text-[13px] font-medium text-gray-500 mt-0.5">你的备考总控台</p>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5 no-scrollbar">
          
          {/* Core State */}
          <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] relative overflow-hidden group shrink-0">
            <div className="absolute inset-0 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 opacity-[0.03]" />
            <div className="relative z-10 flex flex-col items-center text-center gap-3">
              <div className="w-16 h-16 rounded-[24px] bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shadow-lg shadow-purple-500/20">
                <Brain size={32} className="text-white" />
              </div>
              <p className="text-[15px] text-gray-900 font-medium mt-1">我已经同步了你的岗位、能力和今天的计划。</p>
              
              <div className="flex flex-wrap justify-center gap-2 mt-2">
                <button onClick={() => onGo("placeholder")} className="px-3 py-1.5 bg-gray-50 border border-gray-100 rounded-full text-[11px] font-medium text-gray-500 active:scale-95">主目标已确认</button>
                <button onClick={() => onGo("placeholder")} className="px-3 py-1.5 bg-gray-50 border border-gray-100 rounded-full text-[11px] font-medium text-gray-500 active:scale-95">今日 3 项任务</button>
                <button onClick={() => onGo("placeholder")} className="px-3 py-1.5 bg-gray-50 border border-gray-100 rounded-full text-[11px] font-medium text-gray-500 active:scale-95">2 项待复测</button>
                <button onClick={() => onGo("placeholder")} className="px-3 py-1.5 bg-gray-50 border border-gray-100 rounded-full text-[11px] font-medium text-gray-500 active:scale-95">当前可用 2.5 小时</button>
              </div>
            </div>
          </div>

          {/* Primary Action */}
          {viewState === "default" && (
            <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.06)] border border-indigo-50 shrink-0">
              <div className="flex items-center gap-2 mb-3">
                <Sparkles size={16} className="text-indigo-500" />
                <h3 className="font-bold text-[15px] text-gray-900">岸岸发现</h3>
              </div>
              <p className="text-[14px] text-gray-700 leading-relaxed mb-5">
                数量关系已经连续 3 次超时，但今天的计划仍安排了新题。建议把其中 20 分钟改成错题复测。
              </p>
              <div className="flex gap-3">
                <button onClick={() => setDrawerOpen(true)} className="flex-1 py-3 bg-indigo-50 text-indigo-600 rounded-[16px] font-bold text-[14px] active:scale-95 transition-transform">
                  预览调整
                </button>
                <button onClick={() => onGo("placeholder")} className="flex-1 py-3 bg-gray-50 text-gray-500 rounded-[16px] font-bold text-[14px] active:scale-95 transition-transform">
                  先不处理
                </button>
              </div>
            </div>
          )}

          {viewState === "result" && (
            <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.06)] border border-green-50 shrink-0">
              <div className="flex items-center gap-2 mb-3">
                <CheckCircle2 size={16} className="text-green-500" />
                <h3 className="font-bold text-[15px] text-gray-900">今天的计划已重新安排</h3>
              </div>
              <div className="flex flex-col gap-2 mb-5">
                <p className="text-[13px] text-gray-600 font-medium">· <span className="text-gray-900 font-bold">新增</span>：数量关系错题复测</p>
                <p className="text-[13px] text-gray-600 font-medium">· <span className="text-gray-900 font-bold">缩短</span>：资料分析练习 10 分钟</p>
                <p className="text-[13px] text-gray-600 font-medium">· <span className="text-gray-900 font-bold">下次检查</span>：今晚完成任务后</p>
              </div>
              <div className="flex gap-3">
                <button onClick={() => onGo("today")} className="flex-1 py-3 bg-gray-900 text-white rounded-[16px] font-bold text-[14px] active:scale-95 transition-transform">
                  查看今天
                </button>
                <button onClick={() => setViewState("default")} className="flex-1 py-3 bg-gray-50 text-gray-500 rounded-[16px] font-bold text-[14px] active:scale-95 transition-transform">
                  撤回本次调整
                </button>
              </div>
            </div>
          )}

          {viewState === "explanation" && (
            <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.06)] border border-indigo-50 shrink-0">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-bold text-[16px] text-gray-900">结论</h3>
                <button onClick={() => setViewState("default")} className="text-[12px] font-medium text-gray-400">关闭</button>
              </div>
              <p className="text-[14px] text-gray-700 leading-relaxed mb-5">
                今天没有安排申论，是因为你只有 1 小时可用，且两项行测复测已经到期。
              </p>
              <div className="bg-[#F7F9FC] rounded-[20px] p-4 flex flex-col gap-2">
                <h4 className="text-[12px] font-bold text-gray-400 mb-1">下一步</h4>
                <button onClick={() => setViewState("default")} className="text-left text-[14px] font-medium text-gray-900 py-2">保持当前计划</button>
                <div className="h-px w-full bg-gray-100" />
                <button onClick={() => setDrawerOpen(true)} className="text-left text-[14px] font-medium text-indigo-600 py-2">预览加入 30 分钟申论</button>
                <div className="h-px w-full bg-gray-100" />
                <button onClick={() => onGo("today")} className="text-left text-[14px] font-medium text-gray-900 py-2">调整今日可用时间</button>
              </div>
            </div>
          )}

          {/* Contextual Pills */}
          <div className="flex flex-col gap-2 shrink-0">
            <h3 className="font-bold text-[14px] text-gray-900 mb-1 ml-1">建议操作</h3>
            <div className="flex flex-wrap gap-2">
              {[
                "今晚只能学 1 小时，重新安排",
                "生成一组数量关系复测",
                "解释为什么今天没有申论",
                "把周六安排成模考日",
                "检查这个岗位我是否能报"
              ].map(opt => (
                <button 
                  key={opt} 
                  onClick={() => {
                    if (opt === "今晚只能学 1 小时，重新安排") setDrawerOpen(true);
                    else if (opt === "解释为什么今天没有申论") setViewState("explanation");
                    else onGo("placeholder");
                  }} 
                  className="px-4 py-2.5 bg-white shadow-[0_2px_10px_rgba(0,0,0,0.02)] rounded-[16px] text-[13px] font-medium text-gray-700 active:scale-95 transition-transform text-left"
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>

          {/* In Progress */}
          <div className="shrink-0 mt-2">
            <h3 className="font-bold text-[14px] text-gray-900 mb-3 ml-1">正在推进</h3>
            <div className="flex flex-col gap-3">
              <button onClick={() => onGo("placeholder")} className="flex items-center justify-between bg-white rounded-[20px] px-5 py-4 shadow-[0_2px_10px_rgba(0,0,0,0.02)] active:scale-95 transition-transform">
                <span className="font-bold text-[14px] text-gray-900">今日计划</span>
                <span className="text-[12px] font-medium text-gray-500 bg-gray-50 px-2 py-1 rounded-md">已完成 1/3</span>
              </button>
              <button onClick={() => onGo("placeholder")} className="flex items-center justify-between bg-white rounded-[20px] px-5 py-4 shadow-[0_2px_10px_rgba(0,0,0,0.02)] active:scale-95 transition-transform">
                <span className="font-bold text-[14px] text-gray-900">数量关系复测</span>
                <span className="text-[12px] font-medium text-indigo-500 bg-indigo-50 px-2 py-1 rounded-md">明天到期</span>
              </button>
              <button onClick={() => onGo("placeholder")} className="flex items-center justify-between bg-white rounded-[20px] px-5 py-4 shadow-[0_2px_10px_rgba(0,0,0,0.02)] active:scale-95 transition-transform">
                <span className="font-bold text-[14px] text-gray-900">周六模考</span>
                <span className="text-[12px] font-medium text-gray-500 bg-gray-50 px-2 py-1 rounded-md">已安排</span>
              </button>
            </div>
          </div>
          
        </div>

        {/* Input Area */}
        <div className="px-5 py-4 pb-6 shrink-0 bg-[#F7F9FC]">
          <div className="relative">
            <input 
              placeholder="告诉岸岸你想完成或改变什么..." 
              className="w-full h-14 pl-5 pr-14 rounded-full bg-white shadow-[0_8px_30px_rgba(0,0,0,0.06)] text-[15px] text-gray-900 font-medium placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 border border-white/60"
            />
            <button onClick={() => onGo("placeholder")} className="absolute right-2 top-2 bottom-2 w-10 bg-gray-900 rounded-full flex items-center justify-center shadow-md shadow-gray-900/20 active:scale-[0.95] transition-transform">
              <ArrowRight size={18} className="text-white" />
            </button>
          </div>
        </div>

      </div>

      {/* Preview Drawer */}
      <AnimatePresence>
        {drawerOpen && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-0 z-50 bg-gray-900/20 backdrop-blur-sm flex flex-col justify-end"
            onClick={() => setDrawerOpen(false)}
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
                <h3 className="text-[20px] font-bold text-gray-900">岸岸准备这样调整</h3>
                <button onClick={() => setDrawerOpen(false)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center">
                  <X size={16} className="text-gray-500" />
                </button>
              </div>
              
              <div className="space-y-5">
                <div>
                  <p className="text-[14px] font-bold text-gray-400 mb-3">调整前</p>
                  <div className="flex flex-col gap-2">
                    <div className="flex justify-between items-center bg-gray-50 px-4 py-3 rounded-[16px] opacity-70">
                      <span className="text-[14px] font-medium text-gray-600 line-through">资料分析新题</span>
                      <span className="text-[13px] text-gray-400">30 分钟</span>
                    </div>
                    <div className="flex justify-between items-center bg-gray-50 px-4 py-3 rounded-[16px] opacity-70">
                      <span className="text-[14px] font-medium text-gray-600 line-through">言语练习</span>
                      <span className="text-[13px] text-gray-400">20 分钟</span>
                    </div>
                  </div>
                </div>

                <div>
                  <p className="text-[14px] font-bold text-indigo-500 mb-3">调整后</p>
                  <div className="flex flex-col gap-2">
                    <div className="flex justify-between items-center bg-indigo-50/50 border border-indigo-100 px-4 py-3 rounded-[16px]">
                      <span className="text-[14px] font-bold text-indigo-900">数量关系错题复测</span>
                      <span className="text-[13px] font-bold text-indigo-500">20 分钟</span>
                    </div>
                    <div className="flex justify-between items-center bg-white border border-gray-100 px-4 py-3 rounded-[16px]">
                      <span className="text-[14px] font-medium text-gray-900">资料分析练习</span>
                      <span className="text-[13px] font-medium text-gray-500">20 分钟</span>
                    </div>
                    <div className="flex justify-between items-center bg-white border border-gray-100 px-4 py-3 rounded-[16px]">
                      <span className="text-[14px] font-medium text-gray-900">言语练习</span>
                      <span className="text-[13px] font-medium text-gray-500">10 分钟</span>
                    </div>
                  </div>
                </div>
                
                <p className="text-[13px] text-gray-500 leading-relaxed bg-gray-50 p-3 rounded-[16px]">
                  总时长仍为 50 分钟，优先处理已经到期的复测。
                </p>
              </div>

              <div className="flex gap-3 mt-6">
                <button 
                  onClick={() => { setDrawerOpen(false); setViewState("result"); }}
                  className="flex-1 py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] active:scale-[0.98] transition-transform shadow-[0_4px_20px_rgba(0,0,0,0.1)]"
                >
                  确认调整
                </button>
                <button 
                  onClick={() => setDrawerOpen(false)}
                  className="w-[100px] py-4 bg-gray-50 text-gray-500 rounded-[20px] font-bold text-[15px] active:scale-[0.98] transition-transform"
                >
                  暂不调整
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};


// ═══════════════ PROFILE TAB ═════════════════════════════════════════════════
const ProfileTab = ({ onGo }: { onGo: (page: Page) => void }) => {
  const [sheet, setSheet] = useState<"none" | "profile" | "archive" | "time" | "reminders" | "privacy">("none");
  const [workdayTime, setWorkdayTime] = useState(2.5);
  const [weekendTime, setWeekendTime] = useState(6.0);
  const [aiCoach, setAiCoach] = useState(true);
  const [reminders, setReminders] = useState({ today: true, expire: true, weekly: true, wechat: true });

  const close = () => setSheet("none");

  const timePresets = [1, 1.5, 2, 2.5, 3, 4, 6, 8, 10];

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
      <div className="relative z-10 flex flex-col h-full">
        <StatusBar />
        <WechatCapsule />
        
        <div className="flex-1 overflow-y-auto px-5 pt-[52px] pb-8 flex flex-col no-scrollbar">
          
          {/* 1. Top Identity Area */}
          <div className="flex items-start justify-between mt-2">
            <div className="flex items-center gap-4">
              <div className="w-16 h-16 rounded-[24px] bg-white border border-gray-100 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex items-center justify-center shrink-0">
                 <User size={28} className="text-gray-300" strokeWidth={2} />
              </div>
              <div>
                <h2 className="text-[22px] font-bold text-gray-900 tracking-tight flex items-center gap-2">
                  林小鱼 
                  <button onClick={() => setSheet("profile")} className="active:scale-95 p-1"><Edit3 size={14} className="text-gray-400" /></button>
                </h2>
                <p className="text-[13px] font-medium text-gray-500 mt-0.5">国考备考第 <span className="font-bold text-gray-900">97</span> 天</p>
                <div className="inline-flex mt-2 items-center px-2 py-1 bg-white border border-gray-100 rounded-lg shadow-sm">
                  <span className="text-[11px] font-bold text-gray-600">2026 国考 · 国家统计局研究室文秘</span>
                </div>
              </div>
            </div>
          </div>

          {/* 2. Exam Profile Summary */}
          <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] mt-6 shrink-0 border border-transparent">
            <div className="flex justify-between items-center mb-5">
              <h3 className="text-[16px] font-bold text-gray-900">备考档案</h3>
              <button onClick={() => setSheet("archive")} className="text-[12px] text-gray-400 font-medium flex items-center gap-0.5 active:scale-95 transition-transform">
                查看和修改 <ChevronRight size={14} />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-y-5 gap-x-4">
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-400 font-medium">学历与专业</span>
                <span className="text-[14px] font-bold text-gray-900">本科 · 汉语言文学</span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-400 font-medium">考生身份</span>
                <span className="text-[14px] font-bold text-gray-900">非应届毕业生</span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-400 font-medium">政治面貌</span>
                <span className="text-[14px] font-bold text-gray-900">共青团员</span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-400 font-medium">户籍与意向地区</span>
                <span className="text-[14px] font-bold text-gray-900">北京 · 全国皆可</span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-400 font-medium">工作经历</span>
                <span className="text-[14px] font-bold text-gray-900">2 年企业工作经历</span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-[11px] text-gray-400 font-medium">服务基层项目经历</span>
                <span className="text-[14px] font-bold text-gray-900">无</span>
              </div>
            </div>
          </div>

          {/* 3. Target Position */}
          <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] mt-4 shrink-0">
            <div className="flex justify-between items-start mb-4">
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-[10px] font-bold text-green-600 bg-green-50 px-2 py-0.5 rounded-md">资格已初验</span>
                </div>
                <h3 className="text-[18px] font-bold text-gray-900">研究室文秘</h3>
                <p className="text-[13px] text-gray-500 font-medium mt-1">国家统计局 · 北京市</p>
              </div>
            </div>
            <div className="flex gap-3">
              <button className="flex-1 py-3 bg-[#F7F9FC] text-gray-700 rounded-[16px] font-bold text-[13px] active:scale-95 transition-transform">
                重新选择岗位
              </button>
              <button className="flex-1 py-3 bg-[#F7F9FC] text-gray-700 rounded-[16px] font-bold text-[13px] active:scale-95 transition-transform">
                管理备选岗位
              </button>
            </div>
          </div>

          {/* 4. Study Mode */}
          <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] mt-4 shrink-0">
            <div className="flex justify-between items-center mb-5">
              <div className="flex items-center gap-2">
                <h3 className="text-[16px] font-bold text-gray-900">学习方式</h3>
                <span className="text-[11px] font-bold text-gray-500 bg-gray-100 px-2 py-0.5 rounded-md">在职备考</span>
              </div>
              <button onClick={() => setSheet("time")} className="text-[12px] text-gray-400 font-medium flex items-center gap-0.5 active:scale-95 transition-transform">
                调整 <ChevronRight size={14} />
              </button>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex-1 bg-[#F7F9FC] py-4 px-2 rounded-[20px] text-center">
                <p className="text-[11px] text-gray-500 font-medium mb-1 whitespace-nowrap">工作日</p>
                <p className="text-[18px] font-bold text-gray-900 tracking-tight">{workdayTime} <span className="text-[11px] text-gray-500 font-normal ml-0.5">h</span></p>
              </div>
              <div className="flex-1 bg-[#F7F9FC] py-4 px-2 rounded-[20px] text-center">
                <p className="text-[11px] text-gray-500 font-medium mb-1 whitespace-nowrap">周末</p>
                <p className="text-[18px] font-bold text-gray-900 tracking-tight">{weekendTime} <span className="text-[11px] text-gray-500 font-normal ml-0.5">h</span></p>
              </div>
              <div className="flex-1 bg-[#F7F9FC] py-4 px-2 rounded-[20px] text-center">
                <p className="text-[11px] text-gray-500 font-medium mb-1 whitespace-nowrap">常用时段</p>
                <p className="text-[15px] font-bold text-gray-900 mt-1.5 tracking-tight">20:00</p>
              </div>
            </div>
          </div>

          {/* 5. Reminders & AI Coach */}
          <div className="bg-white rounded-[32px] px-6 py-2 shadow-[0_8px_30px_rgba(0,0,0,0.04)] mt-4 shrink-0">
            <button onClick={() => setSheet("reminders")} className="w-full flex items-center justify-between py-4 active:scale-[0.98] transition-all">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-[16px] bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shadow-sm shadow-purple-500/20">
                  <Sparkles size={18} className="text-white" />
                </div>
                <div className="text-left">
                  <p className="text-[15px] font-bold text-gray-900">AI 督学与提醒</p>
                  <p className="text-[12px] text-gray-400 font-medium mt-0.5">任务提醒 · 勿扰 · 微信订阅</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-bold text-gray-400">{aiCoach ? '已开启' : '已关闭'}</span>
                <ChevronRight size={16} className="text-gray-300" />
              </div>
            </button>
          </div>

          {/* 6. Materials & Assets */}
          <div className="shrink-0 mt-6 mb-2">
            <h3 className="font-bold text-[15px] text-gray-900 mb-4 tracking-tight ml-2">学习资产</h3>
            <div className="grid grid-cols-5 gap-y-4 gap-x-2">
              {[
                { icon: Book, label: "资料库" },
                { icon: PenTool, label: "错题与反思" },
                { icon: Layers, label: "知识卡" },
                { icon: FileText, label: "申论记录" },
                { icon: Award, label: "模考成绩" },
              ].map(item => (
                <button key={item.label} className="flex flex-col items-center gap-2 active:scale-95 transition-transform group">
                  <div className="w-12 h-12 rounded-[20px] bg-white shadow-[0_4px_20px_rgba(0,0,0,0.03)] flex items-center justify-center group-hover:shadow-[0_8px_30px_rgba(0,0,0,0.06)] transition-all">
                    <item.icon size={20} className="text-gray-700" strokeWidth={1.5} />
                  </div>
                  <span className="text-[11px] font-medium text-gray-600">{item.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* 7. Data & Services */}
          <div className="shrink-0 mt-6 pb-4">
            <h3 className="font-bold text-[15px] text-gray-900 mb-4 tracking-tight ml-2">数据与服务</h3>
            <div className="bg-white rounded-[32px] p-3 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex flex-col gap-1">
              {[
                { icon: Shield, label: "数据与隐私", onClick: () => setSheet("privacy") },
                { icon: Download, label: "导出我的数据", onClick: () => {} },
                { icon: Trash2, label: "删除个人数据", onClick: () => {}, danger: true },
                { icon: Key, label: "授权管理", onClick: () => {} },
                { icon: Headphones, label: "联系客服", onClick: () => {} },
                { icon: Info, label: "关于岸岸", onClick: () => {} },
              ].map(item => (
                <button key={item.label} onClick={item.onClick} className="w-full flex items-center justify-between p-3 rounded-[24px] hover:bg-gray-50 active:scale-[0.98] transition-all">
                  <div className="flex items-center gap-3">
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center ${item.danger ? 'bg-red-50' : 'bg-[#F7F9FC]'}`}>
                      <item.icon size={14} className={item.danger ? "text-red-500" : "text-gray-600"} />
                    </div>
                    <span className={`text-[14px] font-bold ${item.danger ? "text-red-500" : "text-gray-900"}`}>{item.label}</span>
                  </div>
                  <ChevronRight size={16} className="text-gray-300" />
                </button>
              ))}
            </div>
          </div>
          
        </div>
      </div>

      {/* Shared Bottom Sheet Overlay */}
      <AnimatePresence>
        {sheet !== "none" && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-0 z-50 bg-gray-900/20 backdrop-blur-sm flex flex-col justify-end"
            onClick={close}
          >
            <motion.div 
              initial={{ y: "100%" }}
              animate={{ y: 0 }}
              exit={{ y: "100%" }}
              transition={{ type: "spring", damping: 25, stiffness: 200 }}
              className="bg-white rounded-t-[32px] p-6 pt-3 pb-safe max-h-[85vh] overflow-y-auto no-scrollbar"
              onClick={e => e.stopPropagation()}
            >
              <div className="w-10 h-1.5 bg-gray-200 rounded-full mx-auto mb-6 shrink-0" />
              
              {/* Sheet: Profile Edit */}
              {sheet === "profile" && (
                <div>
                  <div className="flex justify-between items-center mb-6">
                    <h3 className="text-[20px] font-bold text-gray-900">编辑资料</h3>
                    <button onClick={close} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center"><X size={16} className="text-gray-500" /></button>
                  </div>
                  <div className="flex flex-col items-center gap-4 mb-8">
                    <div className="w-20 h-20 rounded-[28px] bg-[#F7F9FC] border border-gray-100 flex items-center justify-center relative">
                      <User size={32} className="text-gray-400" />
                      <div className="absolute -bottom-2 -right-2 w-8 h-8 bg-white shadow-md rounded-full border border-gray-50 flex items-center justify-center">
                        <Camera size={14} className="text-gray-900" />
                      </div>
                    </div>
                  </div>
                  <div className="space-y-4">
                    <div className="bg-[#F7F9FC] rounded-[20px] p-4 flex justify-between items-center">
                      <span className="text-[14px] font-medium text-gray-500">微信昵称</span>
                      <span className="text-[15px] font-bold text-gray-900">林小鱼</span>
                    </div>
                  </div>
                  <button onClick={close} className="w-full py-4 mt-8 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] active:scale-[0.98]">保存修改</button>
                </div>
              )}

              {/* Sheet: Archive Edit */}
              {sheet === "archive" && (
                <div>
                  <div className="flex justify-between items-center mb-6">
                    <h3 className="text-[20px] font-bold text-gray-900">备考档案</h3>
                    <button onClick={close} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center"><X size={16} className="text-gray-500" /></button>
                  </div>
                  <div className="space-y-3">
                    {["学历与专业", "考生身份", "政治面貌", "工作经历", "服务基层项目经历", "户籍与意向地区"].map((label) => (
                      <div key={label} className="bg-[#F7F9FC] rounded-[24px] p-4 flex justify-between items-center active:scale-[0.99] transition-transform cursor-pointer">
                        <span className="text-[14px] font-medium text-gray-600">{label}</span>
                        <ChevronRight size={16} className="text-gray-400" />
                      </div>
                    ))}
                  </div>
                  <button onClick={close} className="w-full py-4 mt-6 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] active:scale-[0.98]">完成</button>
                </div>
              )}

              {/* Sheet: Time Adjustment */}
              {sheet === "time" && (
                <div>
                  <div className="flex justify-between items-center mb-6">
                    <h3 className="text-[20px] font-bold text-gray-900">调整学习时间</h3>
                    <button onClick={close} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center"><X size={16} className="text-gray-500" /></button>
                  </div>
                  
                  <div className="mb-8">
                    <div className="flex justify-between items-center mb-4">
                      <span className="text-[15px] font-bold text-gray-900">工作日学习</span>
                      <span className="text-[18px] font-bold text-indigo-600">{workdayTime} 小时</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {timePresets.map(t => (
                        <button 
                          key={`wd-${t}`} 
                          onClick={() => setWorkdayTime(t)}
                          className={`px-4 py-2.5 rounded-[16px] text-[14px] font-bold transition-colors ${workdayTime === t ? "bg-gray-900 text-white" : "bg-[#F7F9FC] text-gray-600 border border-transparent hover:border-gray-200"}`}
                        >
                          {t}h
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="mb-8">
                    <div className="flex justify-between items-center mb-4">
                      <span className="text-[15px] font-bold text-gray-900">周末学习</span>
                      <span className="text-[18px] font-bold text-indigo-600">{weekendTime} 小时</span>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {timePresets.map(t => (
                        <button 
                          key={`we-${t}`} 
                          onClick={() => setWeekendTime(t)}
                          className={`px-4 py-2.5 rounded-[16px] text-[14px] font-bold transition-colors ${weekendTime === t ? "bg-gray-900 text-white" : "bg-[#F7F9FC] text-gray-600 border border-transparent hover:border-gray-200"}`}
                        >
                          {t}h
                        </button>
                      ))}
                    </div>
                  </div>

                  <button onClick={close} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] active:scale-[0.98]">确认调整</button>
                </div>
              )}

              {/* Sheet: Reminders & AI Coach */}
              {sheet === "reminders" && (
                <div>
                  <div className="flex justify-between items-center mb-6">
                    <h3 className="text-[20px] font-bold text-gray-900">AI 督学与提醒</h3>
                    <button onClick={close} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center"><X size={16} className="text-gray-500" /></button>
                  </div>
                  
                  <div className="bg-indigo-50/50 border border-indigo-50 rounded-[24px] p-5 mb-6 flex items-center justify-between">
                    <div>
                      <h4 className="font-bold text-[15px] text-gray-900">AI 督学</h4>
                      <p className="text-[12px] text-gray-500 mt-1">岸岸将根据你的计划自动发送提醒</p>
                    </div>
                    <button onClick={() => setAiCoach(!aiCoach)} className={`w-12 h-7 rounded-full flex items-center p-1 transition-colors ${aiCoach ? 'bg-indigo-500' : 'bg-gray-200'}`}>
                      <motion.div animate={{ x: aiCoach ? 20 : 0 }} className="w-5 h-5 rounded-full bg-white shadow-sm" />
                    </button>
                  </div>

                  <div className="space-y-2 mb-6">
                    {[
                      { key: 'today', label: "今日任务提醒" },
                      { key: 'expire', label: "到期复测提醒" },
                      { key: 'weekly', label: "每周复盘提醒" },
                      { key: 'wechat', label: "微信消息订阅" },
                    ].map(item => (
                      <div key={item.key} className="bg-[#F7F9FC] rounded-[20px] px-5 py-4 flex items-center justify-between opacity-100 transition-opacity" style={{ opacity: aiCoach ? 1 : 0.5 }}>
                        <span className="text-[14px] font-bold text-gray-900">{item.label}</span>
                        <button 
                          disabled={!aiCoach}
                          onClick={() => setReminders(r => ({...r, [item.key]: !r[item.key as keyof typeof r]}))} 
                          className={`w-12 h-7 rounded-full flex items-center p-1 transition-colors ${reminders[item.key as keyof typeof reminders] ? 'bg-gray-900' : 'bg-gray-200'}`}
                        >
                          <motion.div animate={{ x: reminders[item.key as keyof typeof reminders] ? 20 : 0 }} className="w-5 h-5 rounded-full bg-white shadow-sm" />
                        </button>
                      </div>
                    ))}
                  </div>

                  <div className="bg-[#F7F9FC] rounded-[24px] p-5 mb-6">
                    <div className="flex justify-between items-center mb-1">
                      <span className="text-[14px] font-bold text-gray-900">勿扰时间</span>
                      <span className="text-[14px] font-bold text-indigo-600">23:00 - 08:00</span>
                    </div>
                    <p className="text-[11px] text-gray-500 mt-1">此时间段内不会发送任何提醒</p>
                  </div>

                  <button onClick={close} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] active:scale-[0.98]">完成</button>
                </div>
              )}

              {/* Sheet: Privacy Placeholder */}
              {sheet === "privacy" && (
                <div>
                  <div className="flex justify-between items-center mb-6">
                    <h3 className="text-[20px] font-bold text-gray-900">数据与隐私</h3>
                    <button onClick={close} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center"><X size={16} className="text-gray-500" /></button>
                  </div>
                  <div className="bg-[#F7F9FC] rounded-[24px] p-6 mb-6">
                    <Shield size={32} className="text-gray-400 mb-4" />
                    <p className="text-[14px] text-gray-700 leading-relaxed font-medium">
                      岸岸承诺保护你的备考数据安全。<br/><br/>
                      你的刷题记录、能力画像及个人档案仅用于提供个性化的学习规划与诊断服务，绝不会提供给任何第三方用于商业用途。
                    </p>
                  </div>
                  <button onClick={close} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] active:scale-[0.98]">了解并同意</button>
                </div>
              )}

            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
// ═══════════════ SUB PAGES (Simplified for brevity but styled to match) ═════


// ═══════════════ CONTEXTUAL AI COMPONENTS ════════════════════════════════════
const TextSelectionToolbar = ({ onAction }: { onAction: (action: string) => void }) => (
  <motion.div 
    initial={{ opacity: 0, y: 10, scale: 0.95 }}
    animate={{ opacity: 1, y: 0, scale: 1 }}
    exit={{ opacity: 0, scale: 0.95 }}
    className="absolute z-50 flex items-center gap-1 bg-gray-900 text-white p-1.5 rounded-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.15)]"
    style={{ top: '35%', left: '10%' }}
  >
    <button onClick={() => onAction("问岸岸")} className="flex items-center gap-1.5 px-3 py-1.5 hover:bg-white/20 rounded-[12px] transition-colors">
      <Sparkles size={14} className="text-indigo-300" />
      <span className="text-[13px] font-bold">问岸岸</span>
    </button>
    <div className="w-px h-4 bg-gray-700 mx-1" />
    <button onClick={() => onAction("解释")} className="px-3 py-1.5 hover:bg-white/20 rounded-[12px] text-[13px] font-medium transition-colors">解释</button>
    <button onClick={() => onAction("为什么")} className="px-3 py-1.5 hover:bg-white/20 rounded-[12px] text-[13px] font-medium transition-colors">为什么</button>
  </motion.div>
);

const ContextAnanSheet = ({ qIndex, selectedAns, selectedText, onClose, onAction }: { qIndex: number, selectedAns?: number, selectedText?: string, onClose: () => void, onAction: (action: string) => void }) => {
  const ansStr = selectedAns !== undefined ? `已选 ${String.fromCharCode(65 + selectedAns)}` : "未作答";
  return (
  <motion.div 
    initial={{ y: "100%" }}
    animate={{ y: 0 }}
    exit={{ y: "100%" }}
    transition={{ type: "spring", damping: 25, stiffness: 200 }}
    className="absolute bottom-0 left-0 right-0 bg-[#F7F9FC] rounded-t-[32px] pt-3 pb-safe z-50 shadow-[0_-8px_40px_rgba(0,0,0,0.08)] border-t border-white"
  >
    <div className="w-10 h-1.5 bg-gray-200 rounded-full mx-auto mb-4" />
    
    <div className="px-5 flex flex-col gap-4">
      <div className="flex justify-between items-start">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-[16px] bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shadow-sm shadow-purple-500/20 shrink-0">
            <Sparkles size={18} className="text-white" />
          </div>
          <div>
            <p className="text-[11px] font-medium text-gray-500 mb-0.5">正在看：资料分析第 {qIndex + 1} 题 · {ansStr} · 用时 82 秒</p>
            <h3 className="text-[16px] font-bold text-gray-900">岸岸解答</h3>
          </div>
        </div>
        <button onClick={onClose} className="w-8 h-8 rounded-full bg-white shadow-sm flex items-center justify-center shrink-0">
          <X size={16} className="text-gray-500" />
        </button>
      </div>

      {selectedText && (
        <div className="bg-indigo-50/50 border-l-2 border-indigo-500 px-3 py-2 rounded-r-[12px]">
          <p className="text-[12px] text-gray-600 font-medium italic line-clamp-2">"{selectedText}"</p>
        </div>
      )}

      <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.03)] flex flex-col gap-3">
        <h4 className="font-bold text-[14px] text-gray-900">推导过程</h4>
        <p className="text-[14px] text-gray-700 leading-relaxed">
          直接计算大数字容易超时且易错。根据材料，部分量约为 181129，总量约为 216784。<br/><br/>
          采用截位估算法：<br/>
          <span className="font-mono bg-gray-50 px-2 py-1 rounded text-indigo-600 font-bold text-[13px] inline-block my-1">181 ÷ 217 ≈ 83.4%</span>
          <br/>
          选项 C (83.6%) 为最接近的选项。
        </p>
        <div className="bg-orange-50/50 border border-orange-100 p-3 rounded-[16px] mt-2">
          <p className="text-[12px] text-orange-800 font-medium">你用时 82 秒，远超建议时间。下次记得先看选项差距，选项差距大可大胆截位。</p>
        </div>
      </div>

      <div className="flex flex-col gap-2 pb-4">
        <button onClick={() => onAction("update_ability")} className="flex items-center justify-between px-5 py-3.5 bg-white rounded-[20px] shadow-[0_2px_10px_rgba(0,0,0,0.02)] active:scale-95 transition-transform border border-transparent hover:border-gray-100">
          <span className="text-[14px] font-bold text-gray-900">记为本题错因并更新能力</span>
          <ChevronRight size={16} className="text-gray-400" />
        </button>
        <button onClick={() => onAction("add_practice")} className="flex items-center justify-between px-5 py-3.5 bg-white rounded-[20px] shadow-[0_2px_10px_rgba(0,0,0,0.02)] active:scale-95 transition-transform border border-transparent hover:border-gray-100">
          <span className="text-[14px] font-bold text-gray-900">加入今晚复练</span>
          <ChevronRight size={16} className="text-gray-400" />
        </button>
      </div>
    </div>
  </motion.div>
  );
};

const ActionPreviewSheet = ({ actionType, onClose, onConfirm }: { actionType: string, onClose: () => void, onConfirm: () => void }) => (
  <motion.div 
    initial={{ y: "100%" }}
    animate={{ y: 0 }}
    exit={{ y: "100%" }}
    transition={{ type: "spring", damping: 25, stiffness: 200 }}
    className="absolute bottom-0 left-0 right-0 bg-white rounded-t-[32px] pt-3 pb-safe z-50 shadow-[0_-8px_40px_rgba(0,0,0,0.08)]"
  >
    <div className="w-10 h-1.5 bg-gray-200 rounded-full mx-auto mb-6" />
    <div className="px-6 pb-2">
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-[20px] font-bold text-gray-900">岸岸准备这样调整</h3>
        <button onClick={onClose} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center">
          <X size={16} className="text-gray-500" />
        </button>
      </div>
      
      <div className="space-y-5">
        <div>
          <p className="text-[14px] font-bold text-gray-400 mb-3">调整后</p>
          <div className="flex flex-col gap-2">
            {actionType === "add_practice" ? (
              <>
                <div className="flex justify-between items-center bg-indigo-50/50 border border-indigo-100 px-4 py-3 rounded-[16px]">
                  <span className="text-[14px] font-bold text-indigo-900">新增 5 道截位估算练习</span>
                  <span className="text-[13px] font-bold text-indigo-500">今晚</span>
                </div>
                <div className="flex justify-between items-center bg-white border border-gray-100 px-4 py-3 rounded-[16px]">
                  <span className="text-[14px] font-medium text-gray-900">预计时长增加</span>
                  <span className="text-[13px] font-medium text-gray-500">10 分钟</span>
                </div>
              </>
            ) : (
              <>
                <div className="flex justify-between items-center bg-indigo-50/50 border border-indigo-100 px-4 py-3 rounded-[16px]">
                  <span className="text-[14px] font-bold text-indigo-900">记录待验证错因证据</span>
                  <span className="text-[13px] font-bold text-indigo-500">资料分析 · 比重计算</span>
                </div>
                <div className="flex justify-between items-center bg-white border border-gray-100 px-4 py-3 rounded-[16px]">
                  <span className="text-[14px] font-medium text-gray-900">能力画像表现</span>
                  <span className="text-[13px] font-medium text-gray-500">暂不修改，等待复测累计</span>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="flex gap-3 mt-8 pb-4">
        <button 
          onClick={onConfirm}
          className="flex-1 py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] active:scale-[0.98] transition-transform shadow-[0_4px_20px_rgba(0,0,0,0.1)]"
        >
          确认执行
        </button>
        <button 
          onClick={onClose}
          className="w-[100px] py-4 bg-gray-50 text-gray-500 rounded-[20px] font-bold text-[15px] active:scale-[0.98] transition-transform"
        >
          暂不
        </button>
      </div>
    </div>
  </motion.div>
);

const ActionResultCard = ({ actionType, onDismiss }: { actionType: string, onDismiss: () => void }) => (
  <motion.div 
    initial={{ opacity: 0, y: 20, scale: 0.95 }}
    animate={{ opacity: 1, y: 0, scale: 1 }}
    exit={{ opacity: 0, scale: 0.95 }}
    className="absolute bottom-[20%] left-6 right-6 bg-white rounded-[32px] p-6 shadow-[0_16px_40px_rgba(0,0,0,0.12)] border border-green-50 z-50"
  >
    <div className="flex items-center gap-2 mb-3">
      <div className="w-8 h-8 rounded-full bg-green-50 flex items-center justify-center">
        <CheckCircle2 size={16} className="text-green-500" />
      </div>
      <h3 className="font-bold text-[16px] text-gray-900">动作已执行</h3>
    </div>
    <div className="flex flex-col gap-2 mb-5">
      {actionType === "add_practice" ? (
        <>
          <p className="text-[13px] text-gray-600 font-medium">· <span className="text-gray-900 font-bold">新增</span>：5道截位估算复练</p>
          <p className="text-[13px] text-gray-600 font-medium">· <span className="text-gray-900 font-bold">时间</span>：已加入今晚计划</p>
        </>
      ) : (
        <>
          <p className="text-[13px] text-gray-600 font-medium">· <span className="text-gray-900 font-bold">已记录</span>：待验证错因</p>
          <p className="text-[13px] text-gray-600 font-medium">· <span className="text-gray-900 font-bold">状态</span>：已加入后续复测队列</p>
        </>
      )}
    </div>
    <div className="flex gap-3">
      <button onClick={onDismiss} className="flex-1 py-3 bg-gray-900 text-white rounded-[16px] font-bold text-[14px] active:scale-95 transition-transform">
        继续做题
      </button>
    </div>
  </motion.div>
);

const mockQuestions = [
  { id: 1, type: "资料分析", stem: "2023年全国一般公共预算非税收入比上年同期约：", options: ["增长 6.4%", "下降 3.7%", "下降 4.5%", "增长 8.7%"] },
  { id: 2, type: "资料分析", stem: "2023年地方一般公共预算本级收入占全国一般公共预算收入的比重约为：", options: ["51.2%", "54.1%", "56.6%", "58.1%"] },
  { id: 3, type: "资料分析", stem: "根据材料，2023年全国一般公共预算收入 216784 亿元，同比增长 6.4%。其中，税收收入 181129 亿元，同比增长 8.7%。请问税收收入占一般公共预算收入的比重约为：", options: ["81.3%", "82.5%", "83.6%", "84.2%"] },
  { id: 4, type: "资料分析", stem: "2023年中央一般公共预算收入增量约为：", options: ["4250 亿元", "4450 亿元", "4650 亿元", "4850 亿元"] },
  { id: 5, type: "资料分析", stem: "以下关于 2023 年公共预算收入的描述，能够从上述资料中推出的是：", options: ["税收收入增幅低于总体增幅", "非税收入占比有所提升", "地方收入占比较上年有所下降", "中央收入增速低于地方收入增速"] },
];

const PracticePage = ({ onGo, onBack }: { onGo: (page: Page) => void, onBack: () => void }) => {
  const [currentQIndex, setCurrentQIndex] = useState(2); // Starts at 3rd question
  const [materialExpanded, setMaterialExpanded] = useState(true);
  
  const [answers, setAnswers] = useState<Record<number, number>>({ 0: 1, 1: 2 });
  const [eliminated, setEliminated] = useState<Record<number, number[]>>({ 2: [0, 3] });
  const [confidence, setConfidence] = useState<Record<number, string>>({ 0: "确定", 1: "有点犹豫" });
  const [marked, setMarked] = useState<Record<number, boolean>>({ 2: true });

  const [showToolbar, setShowToolbar] = useState(false);
  const [showSheet, setShowSheet] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [showResult, setShowResult] = useState(false);
  const [actionType, setActionType] = useState("add_practice");
  const [selectedText, setSelectedText] = useState("");

  const [showList, setShowList] = useState(false);
  const [showDraft, setShowDraft] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [showSubmitCheck, setShowSubmitCheck] = useState(false);

  const q = mockQuestions[currentQIndex];

  const handleTextSelect = () => {
    const text = window.getSelection()?.toString();
    if (text && text.trim().length > 0) {
      setSelectedText(text);
      setShowToolbar(true);
    }
  };

  const handleSelect = (qIdx: number, optIdx: number) => {
    setAnswers(prev => ({ ...prev, [qIdx]: optIdx }));
  };

  const toggleEliminate = (qIdx: number, optIdx: number) => {
    setEliminated(prev => {
      const arr = prev[qIdx] || [];
      if (arr.includes(optIdx)) return { ...prev, [qIdx]: arr.filter(i => i !== optIdx) };
      return { ...prev, [qIdx]: [...arr, optIdx] };
    });
  };

  const toggleMark = (qIdx: number) => {
    setMarked(prev => ({ ...prev, [qIdx]: !prev[qIdx] }));
  };

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
      <div className="bg-white shrink-0 z-20">
        <StatusBar />
        {/* Top Toolbar */}
        <div className="px-4 py-2 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button onClick={onBack} className="p-1 -ml-1 active:scale-95 text-gray-900"><ArrowLeft size={20}/></button>
            <button onClick={() => setShowList(true)} className="flex items-center gap-1.5 active:scale-95">
              <List size={16} className="text-gray-500" />
              <span className="text-[14px] font-bold text-gray-900">{currentQIndex + 1}/5</span>
            </button>
            <button onClick={() => setIsPaused(true)} className="flex items-center gap-1.5 bg-gray-50 px-2 py-1 rounded-md active:scale-95">
              <Clock size={14} className="text-gray-500" />
              <span className="text-[13px] font-medium text-gray-700 font-mono tracking-wider mt-0.5">00:52</span>
              <Pause size={12} className="text-gray-400 ml-1" />
            </button>
          </div>
          <div className="flex items-center gap-4 pr-1">
            <button onClick={() => setShowDraft(true)} className="active:scale-95"><PenTool size={18} className="text-gray-600" /></button>
            <button onClick={() => toggleMark(currentQIndex)} className="active:scale-95"><Bookmark size={18} className={marked[currentQIndex] ? "text-orange-500 fill-orange-500" : "text-gray-400"} /></button>
            <button onClick={() => setShowSheet(true)} className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shadow-sm shadow-purple-500/20 active:scale-95">
              <Sparkles size={13} className="text-white" />
            </button>
          </div>
        </div>
      </div>

      {/* Material Area */}
      <div className="bg-white shadow-[0_4px_20px_rgba(0,0,0,0.02)] shrink-0 z-10 relative">
        {materialExpanded ? (
          <div className="p-5 max-h-[35vh] overflow-y-auto no-scrollbar">
            <div className="flex justify-between items-center mb-4">
              <span className="text-[11px] font-bold text-indigo-600 bg-indigo-50 px-2.5 py-1 rounded-md">资料分析 · 共享材料 (1-5题)</span>
              <button onClick={() => setMaterialExpanded(false)} className="p-1 active:scale-95 bg-gray-50 rounded-full"><ChevronUp size={16} className="text-gray-400" /></button>
            </div>
            <p className="text-[14px] text-gray-800 leading-relaxed font-medium mb-4 text-justify select-text cursor-text selection:bg-indigo-100" onMouseUp={handleTextSelect}>
              2023年，全国一般公共预算收入 216784 亿元，同比增长 6.4%。其中，税收收入 181129 亿元，同比增长 8.7%；非税收入 35655 亿元，同比下降 3.7%。<br/><br/>
              分中央和地方看，中央一般公共预算收入 99566 亿元，同比增长 4.9%；地方一般公共预算本级收入 117218 亿元，同比增长 7.8%。
            </p>
            <div className="border border-gray-100 rounded-[16px] overflow-hidden mb-2">
              <table className="w-full text-[12px] text-left">
                 <thead className="bg-gray-50/80"><tr><th className="p-3 font-bold text-gray-500">主要税收项目</th><th className="p-3 font-bold text-gray-500">金额(亿元)</th><th className="p-3 font-bold text-gray-500">同比(%)</th></tr></thead>
                 <tbody>
                   <tr className="border-t border-gray-50"><td className="p-3 text-gray-700 font-medium">国内增值税</td><td className="p-3 text-gray-900 font-bold">69332</td><td className="p-3 text-gray-600">+42.3</td></tr>
                   <tr className="border-t border-gray-50"><td className="p-3 text-gray-700 font-medium">企业所得税</td><td className="p-3 text-gray-900 font-bold">41098</td><td className="p-3 text-gray-600">-5.9</td></tr>
                   <tr className="border-t border-gray-50"><td className="p-3 text-gray-700 font-medium">个人所得税</td><td className="p-3 text-gray-900 font-bold">14775</td><td className="p-3 text-gray-600">-1.0</td></tr>
                 </tbody>
              </table>
            </div>
          </div>
        ) : (
          <button onClick={() => setMaterialExpanded(true)} className="w-full px-5 py-3.5 flex justify-between items-center hover:bg-gray-50 transition-colors border-b border-gray-50">
            <div className="flex items-center gap-2">
              <FileText size={14} className="text-indigo-400" />
              <span className="text-[12px] font-bold text-gray-600">共享材料 · 点击展开 · 上次读到第 3 段</span>
            </div>
            <ChevronDown size={14} className="text-gray-400" />
          </button>
        )}
      </div>

      {/* Question Area */}
      <div className="flex-1 overflow-y-auto px-5 pt-6 pb-28 z-0 relative">
        <h3 className="text-[16px] font-bold text-gray-900 mb-6 flex items-start gap-2">
          <span className="shrink-0 mt-0.5">{currentQIndex + 1}.</span>
          <span className="leading-relaxed cursor-text selection:bg-indigo-100" onMouseUp={handleTextSelect}>
            {q.stem}
          </span>
        </h3>
        
        <div className="flex flex-col gap-3">
          {q.options.map((opt, i) => {
            const isSelected = answers[currentQIndex] === i;
            const isEliminated = eliminated[currentQIndex]?.includes(i);
            
            return (
              <div key={i} className="flex items-stretch gap-2">
                <button 
                  onClick={() => {
                    if (isEliminated) toggleEliminate(currentQIndex, i);
                    handleSelect(currentQIndex, i);
                  }}
                  className={`flex-1 p-4 rounded-[20px] text-left transition-all ${
                    isSelected ? 'bg-indigo-50/60 border border-indigo-200 shadow-sm' : 
                    isEliminated ? 'bg-transparent border border-gray-200 opacity-40 grayscale' : 
                    'bg-white border border-gray-100 shadow-[0_2px_10px_rgba(0,0,0,0.02)] hover:border-gray-200'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 font-bold text-[13px] ${
                      isSelected ? 'bg-indigo-500 text-white' : 
                      isEliminated ? 'bg-gray-200 text-gray-400' : 'bg-gray-100 text-gray-500'
                    }`}>
                      {String.fromCharCode(65 + i)}
                    </div>
                    <span 
                      className={`text-[15px] leading-relaxed mt-0.5 cursor-text selection:bg-indigo-100 ${isSelected ? 'text-indigo-900 font-bold' : isEliminated ? 'line-through text-gray-500' : 'text-gray-800 font-medium'}`}
                      onMouseUp={handleTextSelect}
                    >
                      {opt}
                    </span>
                  </div>
                </button>
                
                <button onClick={() => toggleEliminate(currentQIndex, i)} className={`w-[48px] shrink-0 rounded-[20px] flex items-center justify-center active:scale-95 transition-all ${isEliminated ? 'bg-gray-200 text-gray-500' : 'bg-white shadow-[0_2px_10px_rgba(0,0,0,0.02)] border border-gray-100 text-gray-300 hover:text-gray-400'}`}>
                  <MinusCircle size={18} />
                </button>
              </div>
            );
          })}
        </div>

        <AnimatePresence>
          {answers[currentQIndex] !== undefined && !confidence[currentQIndex] && (
            <motion.div initial={{opacity:0, y:10}} animate={{opacity:1, y:0}} className="mt-6 bg-white p-4 rounded-[24px] shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-gray-50">
              <p className="text-[12px] font-bold text-gray-400 mb-3 text-center">刚刚这题选得怎么样？</p>
              <div className="flex gap-2">
                {["基本靠猜", "有点犹豫", "确定"].map(level => (
                  <button key={level} onClick={() => setConfidence({...confidence, [currentQIndex]: level})} className="flex-1 py-3 bg-[#F7F9FC] rounded-[16px] text-[13px] font-bold text-gray-600 active:scale-95 border border-transparent hover:border-gray-100">
                    {level}
                  </button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        
        {confidence[currentQIndex] && (
          <button onClick={() => setConfidence(prev => ({...prev, [currentQIndex]: ''}))} className="mt-4 px-3 py-1.5 bg-gray-50 text-gray-500 rounded-full text-[12px] font-medium active:scale-95 border border-gray-100/50 block">
            信心：{confidence[currentQIndex]} · 修改
          </button>
        )}
      </div>

      {/* Bottom Action Area */}
      <div className="absolute bottom-0 left-0 right-0 bg-white border-t border-gray-100 px-5 py-3 pb-safe z-10 flex justify-between gap-3 shadow-[0_-8px_30px_rgba(0,0,0,0.02)]">
        <button 
          disabled={currentQIndex === 0}
          onClick={() => setCurrentQIndex(prev => prev - 1)}
          className="flex-1 py-4 bg-[#F7F9FC] text-gray-700 rounded-[20px] font-bold text-[15px] disabled:opacity-40 active:scale-[0.98]"
        >
          上一题
        </button>
        {currentQIndex < mockQuestions.length - 1 ? (
          <button 
            onClick={() => setCurrentQIndex(prev => prev + 1)}
            className="flex-1 py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] active:scale-[0.98] shadow-[0_4px_20px_rgba(0,0,0,0.1)]"
          >
            下一题
          </button>
        ) : (
          <button 
            onClick={() => onGo("result_summary")}
            className="flex-1 py-4 bg-indigo-500 text-white rounded-[20px] font-bold text-[15px] active:scale-[0.98] shadow-[0_4px_20px_rgba(0,0,0,0.2)]"
          >
            完成本组
          </button>
        )}
      </div>

      {/* Overlays / Sheets */}
      
      {/* 1. Question List Panel */}
      <AnimatePresence>
        {showList && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-50 bg-gray-900/20 backdrop-blur-sm flex flex-col justify-end"
            onClick={() => setShowList(false)}
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
                <div>
                  <h3 className="text-[20px] font-bold text-gray-900">题目列表</h3>
                  <p className="text-[12px] text-gray-500 font-medium mt-1">已完成 {Object.keys(answers).length}/5 · 存疑 {Object.keys(marked).filter(k => marked[k as any]).length} 题</p>
                </div>
                <button onClick={() => setShowList(false)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center"><X size={16} className="text-gray-500" /></button>
              </div>
              
              <div className="grid grid-cols-5 gap-3 mb-6">
                {mockQuestions.map((_, i) => {
                  const isCurrent = currentQIndex === i;
                  const isAns = answers[i] !== undefined;
                  const isMark = marked[i];
                  return (
                    <button 
                      key={i} 
                      onClick={() => { setCurrentQIndex(i); setShowList(false); }}
                      className={`relative w-12 h-12 rounded-full mx-auto flex items-center justify-center font-bold text-[16px] transition-all
                        ${isCurrent ? 'bg-indigo-500 text-white shadow-md shadow-indigo-500/20' : 
                          isAns ? 'bg-gray-100 text-gray-900' : 'bg-[#F7F9FC] text-gray-400 border border-gray-100'}
                      `}
                    >
                      {i + 1}
                      {isMark && <div className="absolute -top-1 -right-1 w-4 h-4 bg-orange-500 rounded-full border-2 border-white" />}
                    </button>
                  );
                })}
              </div>

              {/* Legend */}
              <div className="flex justify-center items-center gap-4 border-t border-gray-50 pt-4 pb-2">
                <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-indigo-500" /><span className="text-[11px] text-gray-500 font-medium">当前</span></div>
                <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-gray-300" /><span className="text-[11px] text-gray-500 font-medium">已作答</span></div>
                <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full border-2 border-gray-200" /><span className="text-[11px] text-gray-500 font-medium">未作答</span></div>
                <div className="flex items-center gap-1.5"><div className="w-2 h-2 rounded-full bg-orange-500" /><span className="text-[11px] text-gray-500 font-medium">存疑</span></div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 2. Draft Layer */}
      <AnimatePresence>
        {showDraft && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-40 bg-white/40 backdrop-blur-sm border border-white/60 flex flex-col"
          >
            <div className="flex justify-between items-center p-4 pt-[52px] border-b border-white/40">
              <div className="flex items-center gap-1.5 px-3 py-1.5 bg-white/70 shadow-sm rounded-full">
                <div className="w-2 h-2 rounded-full bg-green-500" />
                <span className="text-[12px] font-bold text-gray-700">草稿模式</span>
              </div>
              <div className="flex gap-2">
                <button className="w-8 h-8 flex items-center justify-center bg-white/70 shadow-sm rounded-full active:scale-95"><Undo2 size={14} className="text-gray-600" /></button>
                <button className="w-8 h-8 flex items-center justify-center bg-white/70 shadow-sm rounded-full active:scale-95"><Redo2 size={14} className="text-gray-600" /></button>
                <button className="w-8 h-8 flex items-center justify-center bg-white/70 shadow-sm rounded-full active:scale-95"><Eraser size={14} className="text-gray-600" /></button>
                <button className="w-8 h-8 flex items-center justify-center bg-white/70 shadow-sm rounded-full active:scale-95"><Trash size={14} className="text-gray-600" /></button>
              </div>
            </div>
            <div className="flex-1 relative">
              {/* Fake drawing */}
              <svg className="absolute inset-0 w-full h-full" style={{ pointerEvents: 'none' }}>
                <path d="M 50,150 Q 80,130 100,160 T 150,180" fill="transparent" stroke="#111827" strokeWidth="4" strokeLinecap="round" />
                <text x="50" y="220" fontSize="18" fontFamily="monospace" fontWeight="bold" fill="#111827">181 / 217</text>
              </svg>
            </div>
            <div className="p-4 flex gap-3 border-t border-white/40 bg-white/40 backdrop-blur-md pb-safe">
              <button className="flex-1 py-3.5 bg-white/80 text-gray-800 rounded-[20px] font-bold text-[14px] shadow-sm active:scale-95 flex items-center justify-center gap-1.5">
                <Sparkles size={14} className="text-indigo-600" /> 识别算式
              </button>
              <button onClick={() => setShowDraft(false)} className="flex-1 py-3.5 bg-gray-900 text-white rounded-[20px] font-bold text-[14px] shadow-md shadow-gray-900/20 active:scale-95">
                收起草稿
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 3. Paused State */}
      <AnimatePresence>
        {isPaused && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-50 bg-white/90 backdrop-blur-md flex flex-col items-center justify-center px-6"
          >
            <div className="w-20 h-20 bg-gray-100 rounded-[24px] flex items-center justify-center mb-6">
              <Pause size={32} className="text-gray-400" />
            </div>
            <h2 className="text-[24px] font-bold text-gray-900 mb-2">练习已暂停</h2>
            <p className="text-[14px] font-medium text-gray-500 mb-10">本组已用时 04:12 · 完成 {Object.keys(answers).length}/5</p>
            
            <button onClick={() => setIsPaused(false)} className="w-full max-w-[280px] py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_4px_20px_rgba(0,0,0,0.1)] active:scale-95 transition-transform mb-3">
              继续作答
            </button>
            <button onClick={() => { setIsPaused(false); onBack(); }} className="w-full max-w-[280px] py-4 bg-transparent text-gray-500 rounded-[24px] font-bold text-[15px] active:scale-95 transition-transform border border-gray-200">
              暂存并退出
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 4. Finish Check State */}
      <AnimatePresence>
        {showSubmitCheck && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-50 bg-gray-900/20 backdrop-blur-sm flex flex-col justify-end"
            onClick={() => setShowSubmitCheck(false)}
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
              <div className="text-center mb-8">
                <div className="w-16 h-16 bg-indigo-50 rounded-full flex items-center justify-center mx-auto mb-4">
                  <CheckCircle2 size={28} className="text-indigo-500" />
                </div>
                <h3 className="text-[20px] font-bold text-gray-900 mb-1">完成本组练习？</h3>
                <p className="text-[13px] text-gray-500 font-medium">还有 {5 - Object.keys(answers).length} 题未作答，{Object.keys(marked).filter(k => marked[k as any]).length} 题存疑</p>
              </div>
              
              <div className="flex gap-3">
                <button onClick={() => { setShowSubmitCheck(false); onBack(); }} className="flex-1 py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] active:scale-[0.98] transition-transform shadow-[0_4px_20px_rgba(0,0,0,0.1)]">
                  确认交卷
                </button>
                <button onClick={() => setShowSubmitCheck(false)} className="flex-1 py-4 bg-[#F7F9FC] text-gray-700 rounded-[20px] font-bold text-[15px] active:scale-[0.98] transition-transform">
                  继续检查
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 5. Contextual AI Layers */}
      <AnimatePresence>
        {showToolbar && (
          <TextSelectionToolbar 
            onAction={(action) => {
              setShowToolbar(false);
              setShowSheet(true);
            }} 
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showSheet && (
          <>
            <motion.div initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}} className="absolute inset-0 z-40 bg-gray-900/20 backdrop-blur-sm" onClick={() => setShowSheet(false)} />
            <ContextAnanSheet 
              qIndex={currentQIndex}
              selectedAns={answers[currentQIndex]}
              selectedText={selectedText}
              onClose={() => setShowSheet(false)} 
              onAction={(action) => {
                setActionType(action);
                setShowSheet(false);
                setShowPreview(true);
              }} 
            />
          </>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showPreview && (
          <>
            <motion.div initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}} className="absolute inset-0 z-40 bg-gray-900/20 backdrop-blur-sm" onClick={() => setShowPreview(false)} />
            <ActionPreviewSheet
              actionType={actionType}
              onClose={() => setShowPreview(false)} 
              onConfirm={() => {
                setShowPreview(false);
                setShowResult(true);
              }} 
            />
          </>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showResult && (
          <>
            <motion.div initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}} className="absolute inset-0 z-40 bg-gray-900/20 backdrop-blur-sm" onClick={() => setShowResult(false)} />
            <ActionResultCard onDismiss={() => setShowResult(false)} />
          </>
        )}
      </AnimatePresence>
    </div>
  );
};


const PhotoPage = ({ onBack }: { onBack: () => void }) => (
  <div className="flex flex-col h-full bg-[#F7F9FC]">
    <StatusBar />
    <div className="px-4 pb-2 flex items-center gap-2">
      <button onClick={onBack} className="p-2 -ml-2 rounded-full active:bg-gray-200">
        <ArrowLeft size={24} className="text-gray-900" />
      </button>
      <h2 className="text-[18px] font-bold text-gray-900">拍照搜题</h2>
    </div>
    <div className="flex-1 flex items-center justify-center">
       <p className="text-gray-400 font-medium">相机渲染区域</p>
    </div>
  </div>
);

const EssayPage = ({ onBack }: { onBack: () => void }) => (
  <div className="flex flex-col h-full bg-[#F7F9FC]">
    <StatusBar />
    <div className="px-4 pb-2 flex items-center gap-2">
      <button onClick={onBack} className="p-2 -ml-2 rounded-full active:bg-gray-200">
        <ArrowLeft size={24} className="text-gray-900" />
      </button>
      <h2 className="text-[18px] font-bold text-gray-900">申论作答</h2>
    </div>
    <div className="flex-1 flex items-center justify-center">
       <p className="text-gray-400 font-medium">申论材料与作答区</p>
    </div>
  </div>
);

const PositionPage = ({ onBack, onSelectPosition }: { onBack: () => void, onSelectPosition: (id: string) => void }) => (
  <div className="flex flex-col h-full bg-[#F7F9FC]">
    <StatusBar />
    <div className="px-4 pb-2 flex items-center gap-2">
      <button onClick={onBack} className="p-2 -ml-2 rounded-full active:bg-gray-200">
        <ArrowLeft size={24} className="text-gray-900" />
      </button>
      <h2 className="text-[18px] font-bold text-gray-900">报考档案</h2>
    </div>
    <div className="flex-1 p-5">
      <button onClick={() => onSelectPosition("p1")} className="w-full bg-white rounded-[24px] p-5 shadow-[0_8px_30px_rgba(0,0,0,0.04)] text-left font-bold text-gray-900 active:scale-[0.98] transition-transform">
        查看岗位详情
      </button>
    </div>
  </div>
);

const PositionDetailPage = ({ posId, onBack }: { posId: string, onBack: () => void }) => (
  <div className="flex flex-col h-full bg-[#F7F9FC]">
    <StatusBar />
    <div className="px-4 pb-2 flex items-center gap-2">
      <button onClick={onBack} className="p-2 -ml-2 rounded-full active:bg-gray-200">
        <ArrowLeft size={24} className="text-gray-900" />
      </button>
      <h2 className="text-[18px] font-bold text-gray-900">岗位详情</h2>
    </div>
    <div className="flex-1 flex items-center justify-center">
       <p className="text-gray-400 font-medium">详情展示 {posId}</p>
    </div>
  </div>
);


const PlaceholderPage = ({ onBack }: { onBack: () => void }) => (
  <div className="flex flex-col h-full bg-[#F7F9FC]">
    <StatusBar />
    <WechatCapsule />
    <div className="flex-1 flex flex-col items-center justify-center gap-4 px-5">
      <h2 className="text-[20px] font-bold text-gray-900">功能建设中</h2>
      <p className="text-[13px] text-gray-500 text-center">对应维度的详细分析页尚未设计，敬请期待。</p>
      <button onClick={onBack} className="px-6 py-2.5 bg-gray-900 text-white rounded-full text-[14px] font-semibold active:scale-95 mt-4">返回</button>
    </div>
  </div>
);

// ═══════════════ APP ROOT ════════════════════════════════════════════════════

const ResultSummaryPage = ({ onGo, onBack, onLater }: { onGo: (p: Page) => void, onBack: () => void, onLater?: () => void }) => {
  const [showBasis, setShowBasis] = useState(false);

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
      <StatusBar />
      <WechatCapsule />
      
      <div className="flex-1 overflow-y-auto px-5 pt-[52px] pb-8 flex flex-col gap-5 no-scrollbar z-0 relative">
        <div className="flex items-center gap-2">
          <button onClick={onBack} className="p-2 -ml-2 rounded-full active:bg-gray-200">
            <ArrowLeft size={24} className="text-gray-900" />
          </button>
        </div>
        
        <div className="mb-2">
          <h2 className="text-[28px] font-bold text-gray-900 tracking-tight">这组做完了</h2>
          <p className="text-[14px] text-gray-500 mt-1 font-medium leading-relaxed">比起3/5，更重要的是找到丢掉两题的原因。</p>
        </div>

        {/* Stats Row */}
        <div className="bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] shrink-0">
          <div className="grid grid-cols-5 gap-2 text-center mb-6">
            <div className="flex flex-col gap-1">
              <span className="text-[18px] font-bold text-gray-900">3/5</span>
              <span className="text-[11px] font-medium text-gray-400">正确</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[18px] font-bold text-gray-900">5:36</span>
              <span className="text-[11px] font-medium text-gray-400">用时</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[18px] font-bold text-orange-500">2</span>
              <span className="text-[11px] font-medium text-gray-400">超时</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[18px] font-bold text-gray-900">1</span>
              <span className="text-[11px] font-medium text-gray-400">改答</span>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[18px] font-bold text-gray-900">1</span>
              <span className="text-[11px] font-medium text-gray-400">存疑</span>
            </div>
          </div>

          <div className="bg-[#F7F9FC] rounded-[20px] p-4">
            <h4 className="text-[12px] font-bold text-gray-900 mb-3">信心校准</h4>
            <div className="flex flex-col gap-2.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
                  <span className="text-[13px] font-medium text-gray-600">确定且正确</span>
                </div>
                <span className="text-[13px] font-bold text-gray-900">2 题</span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-indigo-400" />
                  <span className="text-[13px] font-medium text-gray-600">犹豫但正确</span>
                </div>
                <span className="text-[13px] font-bold text-gray-900">1 题</span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-orange-400" />
                  <span className="text-[13px] font-medium text-gray-600">犹豫且错误</span>
                </div>
                <span className="text-[13px] font-bold text-gray-900">1 题</span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-red-400" />
                  <span className="text-[13px] font-medium text-gray-600">基本靠猜且错误</span>
                </div>
                <span className="text-[13px] font-bold text-gray-900">1 题</span>
              </div>
            </div>
          </div>
        </div>

        {/* An'an Insight */}
        <div className="shrink-0 bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.06)] relative overflow-hidden group border border-indigo-50/50">
          <div className="absolute inset-0 bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 opacity-[0.02]" />
          <div className="relative z-10">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={16} className="text-indigo-500" />
              <h3 className="font-bold text-[15px] text-gray-900">岸岸结论</h3>
            </div>
            <p className="text-[14px] text-gray-900 leading-relaxed font-medium mb-1">
              你不是不会比重计算，主要问题是看到大数后直接精算，导致超时并在最后一步改错。
            </p>
            <AnimatePresence>
              {showBasis && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                  <div className="mt-4 flex flex-col gap-2">
                    <div className="flex items-center gap-2 text-[12px] font-medium text-gray-500">
                      <div className="w-1 h-1 rounded-full bg-gray-400" /> 第3题用时 82 秒
                    </div>
                    <div className="flex items-center gap-2 text-[12px] font-medium text-gray-500">
                      <div className="w-1 h-1 rounded-full bg-gray-400" /> 草稿出现完整大数除法
                    </div>
                    <div className="flex items-center gap-2 text-[12px] font-medium text-gray-500">
                      <div className="w-1 h-1 rounded-full bg-gray-400" /> 曾排除正确选项 C，随后改答
                    </div>
                    <div className="flex items-center gap-2 text-[12px] font-medium text-gray-500">
                      <div className="w-1 h-1 rounded-full bg-gray-400" /> 相似问题近 7 天出现 2 次
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
            <button onClick={() => setShowBasis(!showBasis)} className="text-[12px] text-indigo-500 font-medium mt-3 flex items-center gap-1 active:scale-95">
              {showBasis ? "收起结论依据" : "查看结论依据"} <ChevronDown size={12} className={showBasis ? "rotate-180" : ""} />
            </button>
          </div>
        </div>

        {/* Q Strip */}
        <div className="bg-white rounded-[24px] px-5 py-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] shrink-0">
          <div className="flex justify-between items-center gap-2">
            {[
              { id: 1, s: "correct" },
              { id: 2, s: "correct" },
              { id: 3, s: "wrong_timeout" },
              { id: 4, s: "wrong_doubt" },
              { id: 5, s: "correct" },
            ].map((item, i) => (
              <div key={i} className="flex flex-col items-center gap-1.5">
                <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-[14px] ${
                  item.s === "correct" ? "bg-gray-50 text-gray-900 border border-gray-100" :
                  "bg-red-50 text-red-600 border border-red-100"
                }`}>
                  {item.id}
                </div>
                {item.s === "wrong_timeout" && <span className="text-[10px] text-red-500 font-medium scale-90 text-nowrap">复盘·超时</span>}
                {item.s === "wrong_doubt" && <span className="text-[10px] text-red-500 font-medium scale-90 text-nowrap">复盘·存疑</span>}
                {item.s === "correct" && <span className="text-[10px] text-transparent scale-90 text-nowrap">-</span>}
              </div>
            ))}
          </div>
        </div>

        {/* Actions */}
        <div className="flex flex-col gap-3 mt-2 shrink-0">
          <button onClick={() => onGo("result_review")} className="w-full py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] active:scale-[0.98] shadow-[0_4px_20px_rgba(0,0,0,0.1)]">
            复盘 2 道关键题
          </button>
          <div className="flex gap-3">
            <button onClick={() => onLater?.()} className="flex-1 py-3.5 bg-white border border-gray-200 text-gray-700 rounded-[20px] font-bold text-[14px] active:scale-[0.98]">
              稍后复盘
            </button>
            <button onClick={() => onGo("coach")} className="flex-1 py-3.5 bg-indigo-50 border border-indigo-100 text-indigo-600 rounded-[20px] font-bold text-[14px] active:scale-[0.98]">
              带着本组问岸岸
            </button>
          </div>
        </div>

        {/* Ability Disclaimer */}
        <p className="text-[11px] text-gray-400 leading-relaxed text-center px-4 mt-2 mb-6">
          本组新增 5 条作答证据。资料分析能力暂不调整，完成复测后再确认。
        </p>
      </div>
    </div>
  );
};

const ResultReviewPage = ({ onGo, onBack }: { onGo: (p: Page) => void, onBack: () => void }) => {
  const [reason, setReason] = useState("计算策略选择不当");
  const [customReason, setCustomReason] = useState("");
  const [currentReviewIndex, setCurrentReviewIndex] = useState(0);
  const [showOtherOptions, setShowOtherOptions] = useState(false);
  const [showDraftTrace, setShowDraftTrace] = useState(false);

  const reasonsList = [
    "计算策略选择不当", "知识点不会", "题干读错", "列式错误", "计算错误", 
    "时间分配不当", "改答失误", "自己填写"
  ];

  const reviewItems = [
    {
      id: 3,
      stem: "3. 根据材料，2023年全国一般公共预算收入 216784 亿元，同比增长 6.4%。其中，税收收入 181129 亿元，同比增长 8.7%。请问税收收入占一般公共预算收入的比重约为：",
      userAns: "A. 81.3%",
      correctAns: "C. 83.6%",
      time: "用时 82 秒",
      confidence: "信心：基本靠猜",
      traces: ["曾排除了 C", "有改答记录"],
      conclusion: "选项 C 为最接近的正确结果。",
      path: "看选项可知差距大于1%，满足截位估算条件。直接将数据截位为 181 ÷ 217，首位商8，次位估算约为3，直接锁定 83.6%。",
      mistake: "这里不需要计算完整结果，截位到 181÷217 即可。你在草稿中列出了完整的除法算式，导致超时并在最后一步改错。",
      memory: "选项差距大，截位不用怕；精算浪费时间还容易错。",
      otherOptions: [
        { opt: "A", desc: "错误使用了2022年的基期数据进行近似相除。" },
        { opt: "B", desc: "截位时四舍五入进位错误，导致商偏小。" },
        { opt: "D", desc: "错将部分量误认为另一个指标，多算了约 0.6%。" }
      ],
      draftTraces: [
        { label: "完整大数除法", desc: "列出了 181129 ÷ 216784，耗时 45 秒。" },
        { label: "排除C后又改答", desc: "曾在 60 秒时划掉 C 选项，但在最后重新选中 A。" }
      ],
      suggestedReason: "计算策略选择不当"
    },
    {
      id: 4,
      stem: "4. 2023年中央一般公共预算收入增量约为：",
      userAns: "B. 4450 亿元",
      correctAns: "C. 4650 亿元",
      time: "用时 58 秒",
      confidence: "信心：犹豫且错误",
      traces: ["曾排除了 A"],
      conclusion: "选项 C 为正确增量。",
      path: "中央收入为 99566 亿元，增速 4.9%。增量 = 99566 / (1 + 4.9%) * 4.9% ≈ 100000 / 1.05 * 0.05 ≈ 4761 亿元，考虑近似误差，4650 最接近。",
      mistake: "套用公式正确，但在估算 100000 / 1.05 时，折算倍数出现了偏差，导致结果偏小。",
      memory: "增量计算遇小比率，分子分母同估算，注意别把除法变乘法。",
      otherOptions: [
        { opt: "A", desc: "使用了地方收入的增速进行错误计算。" },
        { opt: "B", desc: "计算过程中直接用 99566 * 4.9% 且未除以 (1+r)，导致偏小。" },
        { opt: "D", desc: "分子分母放缩方向错误，导致结果偏大。" }
      ],
      draftTraces: [
        { label: "公式列对算错", desc: "正确写出了 99566/(1+4.9%)*4.9%，但化简错误。" }
      ],
      suggestedReason: "计算错误"
    }
  ];

  const currentItem = reviewItems[currentReviewIndex];
  const scrollRef = React.useRef<HTMLDivElement>(null);

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
      <StatusBar />
      <WechatCapsule />
      
      {/* Minimal Header */}
      <div className="bg-white shrink-0 z-20 pt-12 pb-2 px-4 flex items-center justify-between shadow-[0_4px_20px_rgba(0,0,0,0.02)]">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="p-1 -ml-1 active:scale-95 text-gray-900"><ArrowLeft size={20}/></button>
          <span className="text-[14px] font-bold text-gray-900">单题错因复盘 · {currentReviewIndex + 1}/2</span>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto z-0 relative pb-24 no-scrollbar">
        {/* Material Collapsed */}
        <div className="bg-white px-5 py-3 border-b border-gray-50 flex justify-between items-center active:bg-gray-50 transition-colors">
          <div className="flex items-center gap-2">
            <FileText size={14} className="text-indigo-400" />
            <span className="text-[12px] font-bold text-gray-600">共享材料 · 点击展开 · 上次读到第 3 段</span>
          </div>
          <ChevronDown size={14} className="text-gray-400" />
        </div>

        <div className="p-5 flex flex-col gap-5">
          {/* Question Context */}
          <motion.div key={currentItem.id} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }}>
            <h3 className="text-[15px] font-bold text-gray-900 mb-3 leading-relaxed">
              {currentItem.stem}
            </h3>
            <div className="flex flex-col gap-2">
              <div className="flex justify-between items-center p-3 rounded-[16px] bg-red-50 border border-red-100">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-bold text-red-600">你的答案</span>
                  <span className="text-[13px] font-medium text-red-500">{currentItem.userAns}</span>
                </div>
                <XCircle size={16} className="text-red-500" />
              </div>
              <div className="flex justify-between items-center p-3 rounded-[16px] bg-green-50 border border-green-100">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-bold text-green-700">正确答案</span>
                  <span className="text-[13px] font-medium text-green-600">{currentItem.correctAns}</span>
                </div>
                <CheckCircle2 size={16} className="text-green-600" />
              </div>
            </div>
            
            <div className="flex flex-wrap gap-2 mt-3">
              <span className="px-2 py-1 bg-white border border-gray-100 rounded-md text-[11px] font-medium text-gray-500">{currentItem.time}</span>
              <span className="px-2 py-1 bg-white border border-gray-100 rounded-md text-[11px] font-medium text-gray-500">{currentItem.confidence}</span>
              {currentItem.traces.map((tr, i) => (
                <span key={i} className="px-2 py-1 bg-white border border-gray-100 rounded-md text-[11px] font-medium text-orange-500">{tr}</span>
              ))}
            </div>
          </motion.div>

          {/* Structured Parse */}
          <motion.div key={`parse-${currentItem.id}`} initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-gray-50">
            <div className="flex items-center gap-2 mb-4">
              <Sparkles size={14} className="text-indigo-500" />
              <h4 className="font-bold text-[14px] text-gray-900">岸岸结构化解析</h4>
            </div>
            <div className="space-y-4">
              <div>
                <span className="text-[12px] font-bold text-gray-400 mb-1 block">正确结论</span>
                <p className="text-[13px] text-gray-800 font-medium">{currentItem.conclusion}</p>
              </div>
              <div>
                <span className="text-[12px] font-bold text-gray-400 mb-1 block">最短解题路径</span>
                <p className="text-[13px] text-gray-800 leading-relaxed font-medium">{currentItem.path}</p>
              </div>
              <div className="bg-orange-50 p-3 rounded-[12px] border border-orange-100/50">
                <span className="text-[12px] font-bold text-orange-700 mb-1 block">你的具体错点</span>
                <p className="text-[13px] text-orange-800 leading-relaxed">{currentItem.mistake}</p>
              </div>
              <div>
                <span className="text-[12px] font-bold text-gray-400 mb-1 block">一句话记忆</span>
                <p className="text-[13px] text-indigo-700 font-bold bg-indigo-50 py-2 px-3 rounded-lg inline-block">{currentItem.memory}</p>
              </div>
            </div>
            
            <div className="mt-4 flex flex-col gap-2 pt-4 border-t border-gray-50">
              <div>
                <button onClick={() => setShowOtherOptions(!showOtherOptions)} className="text-[13px] font-medium text-gray-500 flex items-center gap-1 active:scale-95">
                  为什么其他选项不对 <ChevronDown size={14} className={`transition-transform ${showOtherOptions ? "rotate-180" : ""}`} />
                </button>
                <AnimatePresence>
                  {showOtherOptions && (
                    <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                      <div className="mt-2 bg-gray-50 p-3 rounded-xl flex flex-col gap-3">
                        {currentItem.otherOptions.map((optItem, i) => (
                          <div key={i}>
                            <span className="text-[12px] font-bold text-gray-700">{optItem.opt}</span>
                            <p className="text-[12px] text-gray-500 mt-0.5 leading-relaxed">{optItem.desc}</p>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
              
              <div>
                <button onClick={() => setShowDraftTrace(!showDraftTrace)} className="text-[13px] font-medium text-gray-500 flex items-center gap-1 active:scale-95">
                  查看我的草稿轨迹 <ChevronDown size={14} className={`transition-transform ${showDraftTrace ? "rotate-180" : ""}`} />
                </button>
                <AnimatePresence>
                  {showDraftTrace && (
                    <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                      <div className="mt-2 bg-gray-50 p-3 rounded-xl flex flex-col gap-2">
                        {currentItem.draftTraces.map((dt, i) => (
                          <div key={i} className="flex items-start gap-2">
                            <div className="w-1.5 h-1.5 rounded-full bg-orange-400 mt-1.5 shrink-0" />
                            <p className="text-[12px] text-gray-600 leading-relaxed">
                              <span className="font-bold text-gray-900">{dt.label}</span>：{dt.desc}
                            </p>
                          </div>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </div>
          </motion.div>

          {/* Reason Confirmation */}
          <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-gray-50">
            <h4 className="font-bold text-[14px] text-gray-900 mb-1">错因确认</h4>
            <p className="text-[12px] text-gray-500 font-medium mb-4">岸岸建议：<span className="text-gray-900 font-bold">{currentItem.suggestedReason}</span></p>
            <div className="flex flex-wrap gap-2.5">
              {reasonsList.map(r => (
                <button 
                  key={r}
                  onClick={() => setReason(r)}
                  className={`px-3 py-1.5 rounded-[12px] text-[12px] font-bold transition-all active:scale-95 ${
                    reason === r ? "bg-gray-900 text-white shadow-md shadow-gray-900/10" : "bg-[#F7F9FC] text-gray-500 border border-gray-100/50 hover:bg-gray-50"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
            <AnimatePresence>
              {reason === "自己填写" && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                  <input 
                    type="text" 
                    value={customReason} 
                    onChange={e => setCustomReason(e.target.value)} 
                    placeholder="输入具体错因..." 
                    className="mt-3 w-full p-3 bg-[#F7F9FC] border border-gray-100 rounded-[12px] text-[13px] text-gray-900 focus:outline-none focus:border-indigo-200"
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>

        </div>
      </div>

      <div className="absolute bottom-0 left-0 right-0 p-5 bg-white border-t border-gray-50 shadow-[0_-8px_30px_rgba(0,0,0,0.02)] z-20 pb-safe">
        <button 
          onClick={() => {
            if (currentReviewIndex === 0) {
              setCurrentReviewIndex(1);
            } else {
              onGo("result_completed");
            }
          }} 
          className="w-full py-4 bg-gray-900 text-white rounded-[20px] font-bold text-[15px] active:scale-[0.98] shadow-[0_4px_20px_rgba(0,0,0,0.1)]"
        >
          {currentReviewIndex === 0 ? "确认错因，看下一题" : "确认错因，完成复盘"}
        </button>
      </div>
    </div>
  );
};

const ResultCompletedPage = ({ onGo, onBack }: { onGo: (p: Page) => void, onBack: () => void }) => {
  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative overflow-hidden">
      <StatusBar />
      <WechatCapsule />
      
      <div className="flex-1 overflow-y-auto px-5 pt-[60px] pb-8 flex flex-col no-scrollbar items-center text-center">
        
        <div className="w-20 h-20 bg-white rounded-full flex items-center justify-center shadow-[0_8px_30px_rgba(0,0,0,0.04)] mb-6 mt-10 border border-gray-50">
          <CheckCircle2 size={36} className="text-gray-900" />
        </div>

        <h2 className="text-[24px] font-bold text-gray-900 tracking-tight mb-2">复盘完成</h2>
        <p className="text-[14px] text-gray-500 font-medium mb-8 px-6 leading-relaxed">明晚复测会验证这条判断。只有后续多次出现一致证据，才会更新为稳定能力结论。</p>

        <div className="w-full bg-white rounded-[32px] p-6 shadow-[0_8px_30px_rgba(0,0,0,0.04)] flex flex-col gap-4 text-left">
          <div className="flex justify-between items-center border-b border-gray-50 pb-4">
            <span className="text-[14px] font-bold text-gray-600">已确认错因</span>
            <span className="text-[15px] font-bold text-gray-900">2 条</span>
          </div>
          <div className="flex justify-between items-center border-b border-gray-50 pb-4">
            <span className="text-[14px] font-bold text-gray-600">生成知识卡</span>
            <span className="text-[15px] font-bold text-gray-900">1 张</span>
          </div>
          <div className="flex justify-between items-center border-b border-gray-50 pb-4">
            <span className="text-[14px] font-bold text-gray-600">已安排同类复测</span>
            <span className="text-[15px] font-bold text-gray-900">5 道</span>
          </div>
          <div className="flex justify-between items-center pt-1">
            <span className="text-[14px] font-bold text-indigo-600">复测时间</span>
            <span className="text-[15px] font-bold text-indigo-600">明晚 · 预计10分钟</span>
          </div>
        </div>

        <div className="w-full mt-auto pt-10 flex flex-col gap-3">
          <button onClick={() => onGo("today")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] active:scale-[0.98] shadow-[0_4px_20px_rgba(0,0,0,0.1)]">
            完成复盘
          </button>
          <div className="flex gap-3 mt-1">
            <button onClick={() => onGo("placeholder")} className="flex-1 py-3.5 text-[14px] font-bold text-gray-700 bg-white border border-gray-200 rounded-[20px] active:scale-95 shadow-sm">
              查看知识卡
            </button>
            <button onClick={() => onGo("placeholder")} className="flex-1 py-3.5 text-[14px] font-bold text-gray-700 bg-white border border-gray-200 rounded-[20px] active:scale-95 shadow-sm">
              调整复测时间
            </button>
          </div>
          <button onClick={() => onGo("placeholder")} className="w-full py-3 text-[12px] font-medium text-gray-400 active:scale-95 mt-2">
            现在加练 2 题（不计入复测证据）
          </button>
        </div>

      </div>
    </div>
  );
};


const DailySettlementPage = ({ onGo, onBack }: { onGo: (page: Page) => void; onBack: () => void }) => {
  const [step, setStep] = useState<"summary" | "done">("summary");
  const [unfinishedAction, setUnfinishedAction] = useState<string>("移到周日机动日");

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative">
      <StatusBar />
      <WechatCapsule />
      <div className="absolute top-[48px] left-4 z-50">
        <button onClick={onBack} className="w-8 h-8 rounded-full bg-white/50 backdrop-blur-md border border-gray-200/50 flex items-center justify-center active:scale-95 shadow-sm">
          <ChevronLeft size={20} className="text-gray-900" />
        </button>
      </div>

      {step === "summary" ? (
        <div className="flex-1 overflow-y-auto px-5 pt-[104px] pb-32 flex flex-col gap-8 no-scrollbar">
          {/* Header */}
          <div>
            <h1 className="text-[26px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-3">今天到这里，看看真正留下了什么</h1>
            <p className="text-[15px] text-gray-500 leading-relaxed font-medium">不比刷了多少，只看哪些变化值得带到明天。</p>
          </div>

          {/* Top Summary */}
          <div className="flex gap-2">
            <div className="flex-1 bg-white rounded-[20px] p-4 border border-gray-50 shadow-[0_2px_10px_rgba(0,0,0,0.02)]">
              <p className="text-[12px] font-bold text-gray-400 mb-1">原计划</p>
              <p className="text-[18px] font-bold text-gray-900">2.5<span className="text-[13px] text-gray-500 ml-0.5">小时</span></p>
            </div>
            <div className="flex-1 bg-white rounded-[20px] p-4 border border-gray-50 shadow-[0_2px_10px_rgba(0,0,0,0.02)] relative overflow-hidden">
              <div className="absolute inset-0 bg-gradient-to-br from-blue-50 to-transparent" />
              <div className="relative z-10">
                <p className="text-[12px] font-bold text-blue-400 mb-1">实际学习</p>
                <p className="text-[18px] font-bold text-blue-600">2<span className="text-[13px] text-blue-400 ml-0.5">小时</span></p>
              </div>
            </div>
            <div className="flex-1 bg-white rounded-[20px] p-4 border border-gray-50 shadow-[0_2px_10px_rgba(0,0,0,0.02)]">
              <p className="text-[12px] font-bold text-gray-400 mb-1">完成</p>
              <p className="text-[18px] font-bold text-gray-900">2 / 3<span className="text-[13px] text-gray-500 ml-0.5">项</span></p>
            </div>
          </div>

          {/* Part 1: 今天确认了什么 */}
          <div>
            <div className="flex items-center gap-2 mb-4 ml-1">
               <Target size={18} className="text-gray-900" />
               <h3 className="text-[17px] font-bold text-gray-900 tracking-tight">今天确认了什么</h3>
            </div>
            <div className="bg-white rounded-[28px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-gray-50 relative overflow-hidden">
              <div className="absolute left-0 top-0 bottom-0 w-1 bg-cyan-400" />
              <h4 className="text-[15px] font-bold text-gray-900 mb-2 ml-2">资料分析 · 截位估算</h4>
              <p className="text-[14px] text-gray-700 font-medium leading-relaxed mb-4 ml-2">
                5 道识别题完成，平均用时接近 60 秒。
              </p>
              <div className="bg-cyan-50/50 rounded-[16px] p-3 flex gap-2.5 items-start ml-2">
                <Info size={14} className="text-cyan-600 shrink-0 mt-0.5" />
                <p className="text-[12px] text-gray-600 font-medium leading-relaxed">
                  这次表现先记下来，完成后续复测再判断是否稳定。
                </p>
              </div>
            </div>
          </div>

          {/* Part 2: 今天新发现的问题 */}
          <div>
            <div className="flex items-center gap-2 mb-4 ml-1">
               <AlertCircle size={18} className="text-orange-500" />
               <h3 className="text-[17px] font-bold text-gray-900 tracking-tight">今天新发现的问题</h3>
            </div>
            <div className="bg-white rounded-[28px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-orange-50 relative">
              <p className="text-[14px] text-gray-800 font-bold leading-relaxed mb-5">
                言语理解出现 1 道“很有把握但答错”的题。
              </p>
              <div className="flex gap-3">
                <button onClick={() => onGo("placeholder")} className="flex-1 py-3 bg-gray-900 text-white rounded-[16px] text-[13px] font-bold active:scale-95 transition-transform">
                  查看这道题
                </button>
                <button onClick={() => onGo("coach")} className="flex-1 py-3 bg-orange-50 text-orange-600 border border-orange-100/50 rounded-[16px] text-[13px] font-bold active:scale-95 transition-transform flex justify-center items-center gap-1">
                  <Sparkles size={14} /> 交给岸岸解释
                </button>
              </div>
            </div>
          </div>

          {/* Part 3: 没完成的这一项 */}
          <div>
            <div className="flex items-center gap-2 mb-4 ml-1">
               <Layers size={18} className="text-gray-400" />
               <h3 className="text-[17px] font-bold text-gray-900 tracking-tight">没完成的这一项</h3>
            </div>
            <div className="bg-white rounded-[28px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-gray-50 flex flex-col gap-4">
              <div>
                <h4 className="text-[15px] font-bold text-gray-900 mb-1">判断推理复测</h4>
                <p className="text-[13px] text-gray-400 font-medium">约 40 分钟</p>
              </div>
              <p className="text-[13px] text-gray-500 leading-relaxed font-medium bg-[#F7F9FC] p-3 rounded-[16px]">
                没有完成的任务不会自动变成学习欠账。
              </p>

              <div className="flex flex-col gap-2.5 mt-2">
                {[
                  { label: "移到周日机动日", rec: true },
                  { label: "明天再决定", rec: false },
                  { label: "这项不再补", rec: false }
                ].map(opt => (
                  <button 
                    key={opt.label}
                    onClick={() => setUnfinishedAction(opt.label)}
                    className={`flex items-center justify-between p-4 rounded-[20px] transition-all border ${unfinishedAction === opt.label ? "border-gray-900 bg-[#F7F9FC]" : "border-gray-100 bg-white"}`}
                  >
                    <span className={`text-[14px] font-bold ${unfinishedAction === opt.label ? "text-gray-900" : "text-gray-600"}`}>
                      {opt.label}
                    </span>
                    <div className="flex items-center gap-2">
                      {opt.rec && <span className="text-[11px] font-bold text-indigo-500 bg-indigo-50 px-2 py-0.5 rounded-md flex items-center gap-1"><Sparkles size={10} /> 岸岸推荐</span>}
                      <div className={`w-5 h-5 rounded-full border-[1.5px] flex items-center justify-center ${unfinishedAction === opt.label ? "border-gray-900" : "border-gray-300"}`}>
                        {unfinishedAction === opt.label && <div className="w-2.5 h-2.5 bg-gray-900 rounded-full" />}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Part 4: 明天从哪里开始 */}
          <div>
            <div className="flex items-center gap-2 mb-4 ml-1">
               <ArrowRight size={18} className="text-gray-900" />
               <h3 className="text-[17px] font-bold text-gray-900 tracking-tight">明天从哪里开始</h3>
            </div>
            <div className="bg-gradient-to-br from-indigo-50/50 to-blue-50/50 rounded-[28px] p-5 border border-indigo-100/50">
              <span className="text-[11px] font-bold text-indigo-500 bg-indigo-100/50 px-2 py-0.5 rounded-md mb-3 inline-block">预计 20 分钟</span>
              <h4 className="text-[16px] font-bold text-gray-900 mb-2">第一项：言语高信心错题复盘</h4>
              <p className="text-[13px] text-gray-600 font-medium leading-relaxed">
                先解决今天暴露的问题，再继续加量。
              </p>
            </div>
          </div>

        </div>
      ) : (
        <div className="flex-1 px-8 flex flex-col justify-center items-center text-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="flex flex-col items-center"
          >
            <div className="w-20 h-20 bg-white rounded-full flex items-center justify-center shadow-[0_8px_30px_rgba(0,0,0,0.04)] mb-6 border border-gray-50">
              <CheckCircle2 size={36} className="text-green-500" />
            </div>
            <h2 className="text-[24px] font-bold text-gray-900 tracking-tight mb-3">今天已经收好</h2>
            <p className="text-[15px] text-gray-500 font-medium leading-relaxed mb-10 max-w-[240px]">
              计划、做题结果和你的选择都已保存。
            </p>
            
            <button onClick={() => onGo("today")} className="w-[240px] py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform mb-4">
              回到今日
            </button>
            <button onClick={() => onGo("weekly_plan")} className="text-[14px] font-bold text-gray-500 active:scale-95 transition-transform py-2">
              查看本周计划
            </button>
          </motion.div>
        </div>
      )}

      {/* Bottom Action */}
      {step === "summary" && (
        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
          <button onClick={() => setStep("done")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform flex justify-center items-center mb-4">
            确认结算，结束今天
          </button>
          <div className="flex justify-center items-center px-4">
             <button onClick={() => onGo("coach")} className="text-[14px] font-bold text-indigo-500 flex items-center gap-1.5 active:scale-95 transition-transform">
               带着今天的结果问岸岸 <Sparkles size={14} />
             </button>
          </div>
        </div>
      )}
    </div>
  );
};


const TodayAdjustmentPage = ({ onGo, onBack }: { onGo: (page: Page) => void; onBack: () => void }) => {
  const [step, setStep] = useState<"edit" | "preview">("edit");
  const [studyTime, setStudyTime] = useState(2.5);
  const [energy, setEnergy] = useState("正常");

  const updateTime = (delta: number) => {
    setStudyTime(prev => Math.min(10, Math.max(0, prev + delta)));
  };

  return (
    <div className="flex flex-col h-full bg-[#F7F9FC] relative">
      <StatusBar />
      <WechatCapsule />
      <div className="absolute top-[48px] left-4 z-50">
        <button onClick={onBack} className="w-8 h-8 rounded-full bg-white/50 backdrop-blur-md border border-gray-200/50 flex items-center justify-center active:scale-95 shadow-sm">
          <ChevronLeft size={20} className="text-gray-900" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 pt-[104px] pb-32 flex flex-col gap-8 no-scrollbar">
        {step === "edit" ? (
          <>
            {/* Header */}
            <div>
              <h1 className="text-[26px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-3">今天变了，计划也可以跟着变</h1>
              <p className="text-[15px] text-gray-500 leading-relaxed font-medium">告诉岸岸还剩多少时间，先保住最值得做的。</p>
            </div>

            {/* Current Schedule */}
            <div>
              <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 relative overflow-hidden">
                <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-blue-400 to-cyan-400" />
                <h3 className="text-[13px] font-bold text-gray-400 mb-3 ml-2">当前安排</h3>
                <div className="flex gap-2 mb-3 ml-2">
                   <span className="text-[13px] font-bold text-blue-600 bg-blue-50 px-2 py-1 rounded-md">剩余 2.5 小时</span>
                   <span className="text-[13px] font-bold text-gray-600 bg-gray-100 px-2 py-1 rounded-md">3 项任务</span>
                </div>
                <p className="text-[14px] font-medium text-gray-700 ml-2">
                  当前重点：<span className="font-bold text-gray-900">资料分析估算验证</span>
                </p>
              </div>
            </div>

            {/* Time Adjustment */}
            <div>
              <h3 className="text-[17px] font-bold text-gray-900 mb-4 ml-1 tracking-tight">今天还能学多久</h3>
              <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50">
                <div className="flex items-center justify-between bg-[#F7F9FC] rounded-[16px] p-3 mb-5 border border-gray-100">
                   <button onClick={() => updateTime(-0.5)} className="w-10 h-10 rounded-full bg-white shadow-sm flex items-center justify-center text-gray-500 active:scale-95 border border-gray-50">
                     <MinusCircle size={20}/>
                   </button>
                   <div className="flex items-baseline gap-1">
                     <span className="text-[24px] font-bold text-gray-900 leading-none">{studyTime.toFixed(1)}</span>
                     <span className="text-[14px] font-bold text-gray-400">小时</span>
                   </div>
                   <button onClick={() => updateTime(0.5)} className="w-10 h-10 rounded-full bg-white shadow-sm flex items-center justify-center text-gray-900 active:scale-95 border border-gray-50">
                     <PlusCircle size={20}/>
                   </button>
                </div>
                <div className="grid grid-cols-4 gap-2">
                   {[{label: "1 小时", val: 1}, {label: "2 小时", val: 2}, {label: "4 小时", val: 4}, {label: "今天不学", val: 0}].map(opt => (
                     <button key={opt.label} onClick={() => setStudyTime(opt.val)} className="py-2.5 bg-[#F7F9FC] text-gray-600 rounded-[12px] text-[13px] font-bold active:scale-95 transition-transform">
                       {opt.label}
                     </button>
                   ))}
                </div>
              </div>
            </div>

            {/* Energy Adjustment */}
            <div>
              <h3 className="text-[17px] font-bold text-gray-900 mb-4 ml-1 tracking-tight">现在的精力</h3>
              <div className="flex gap-3">
                 {["有点累", "正常", "状态不错"].map(e => (
                   <button key={e} onClick={() => setEnergy(e)} className={`flex-1 py-3.5 rounded-[16px] text-[14px] font-bold transition-all ${energy === e ? "bg-gray-900 text-white shadow-md shadow-gray-900/20" : "bg-white text-gray-600 border border-gray-100 shadow-[0_2px_10px_rgba(0,0,0,0.02)]"}`}>
                     {e}
                   </button>
                 ))}
              </div>
            </div>
          </>
        ) : (
          <>
            {/* Header */}
            <div>
              <h1 className="text-[26px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-4">调整预览</h1>
              <div className="bg-gradient-to-br from-blue-50/80 to-cyan-50/80 border border-cyan-100/50 rounded-[24px] p-5 relative overflow-hidden">
                <div className="absolute top-0 right-0 w-32 h-32 bg-cyan-200/20 rounded-full blur-2xl transform translate-x-1/2 -translate-y-1/2" />
                <div className="relative z-10">
                  <div className="flex items-center gap-2 mb-3">
                    <Sparkles size={18} className="text-cyan-600" />
                    <span className="font-bold text-[16px] text-cyan-900">岸岸建议</span>
                  </div>
                  <p className="text-[14px] text-cyan-900/90 font-medium leading-relaxed">
                    今天只有 {studyTime} 小时，先保留资料分析验证和晚间复盘，判断推理移到周日。
                  </p>
                </div>
              </div>
            </div>

            {/* Task Changes List */}
            <div>
              <h3 className="text-[17px] font-bold text-gray-900 mb-4 ml-1 tracking-tight">本次变动明细</h3>
              <div className="flex flex-col gap-3">
                {/* Retain */}
                <div className="bg-white rounded-[24px] p-4 flex flex-col gap-3 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50">
                   <div className="flex items-center justify-between">
                      <span className="bg-blue-50 text-blue-600 px-2 py-0.5 rounded-md text-[11px] font-bold">保留</span>
                      <span className="text-[13px] font-bold text-gray-900">1 小时</span>
                   </div>
                   <div>
                     <p className="text-[15px] font-bold text-gray-900 mb-1">资料分析验证</p>
                     <p className="text-[13px] text-gray-500 font-medium leading-relaxed">核心目标，优先完成</p>
                   </div>
                </div>

                {/* Shorten */}
                <div className="bg-white rounded-[24px] p-4 flex flex-col gap-3 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50">
                   <div className="flex items-center justify-between">
                      <span className="bg-cyan-50 text-cyan-600 px-2 py-0.5 rounded-md text-[11px] font-bold">缩短</span>
                      <span className="text-[13px] font-bold text-gray-900">0.5 小时</span>
                   </div>
                   <div>
                     <p className="text-[15px] font-bold text-gray-900 mb-1">晚间复盘</p>
                     <p className="text-[13px] text-gray-500 font-medium leading-relaxed">从 45分钟 压缩至 20分钟</p>
                   </div>
                </div>

                {/* Move */}
                <div className="bg-white rounded-[24px] p-4 flex flex-col gap-3 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50">
                   <div className="flex items-center justify-between">
                      <span className="bg-orange-50 text-orange-600 px-2 py-0.5 rounded-md text-[11px] font-bold">移到周日机动日</span>
                      <span className="text-[13px] font-bold text-gray-400 line-through">1 小时</span>
                   </div>
                   <div>
                     <p className="text-[15px] font-bold text-gray-900 mb-1">判断推理</p>
                     <p className="text-[13px] text-gray-500 font-medium leading-relaxed">需要大块时间，周末再做</p>
                   </div>
                </div>

                {/* Drop */}
                <div className="bg-[#F7F9FC] rounded-[24px] p-4 flex flex-col gap-3 border border-gray-100">
                   <div className="flex items-center justify-between">
                      <span className="bg-gray-200 text-gray-600 px-2 py-0.5 rounded-md text-[11px] font-bold">不再补</span>
                   </div>
                   <div>
                     <p className="text-[15px] font-bold text-gray-500 mb-1">常识积累</p>
                     <p className="text-[13px] text-gray-400 font-medium leading-relaxed">低收益任务，本周跳过</p>
                   </div>
                </div>
              </div>
            </div>

            {/* Impact */}
            <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex items-start gap-3">
               <Target size={20} className="text-gray-400 shrink-0 mt-0.5" />
               <div>
                  <p className="text-[15px] font-bold text-gray-900 mb-1">是否影响本周三个目标</p>
                  <p className="text-[13px] text-gray-500 font-medium leading-relaxed">核心进度已保住，不影响目标达成。</p>
               </div>
            </div>

            {/* Disclaimer */}
            <div className="flex items-start gap-2.5 bg-[#F7F9FC] rounded-[20px] p-4">
               <Info size={16} className="text-gray-400 shrink-0 mt-0.5" />
               <p className="text-[13px] text-gray-500 font-medium leading-relaxed">
                 没完成的内容不会自动变成学习欠账。调整后，岸岸会重新为你生成最适合当下的计划。
               </p>
            </div>
          </>
        )}
      </div>

      {/* Bottom Action Area */}
      <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
        {step === "edit" ? (
          <button onClick={() => setStep("preview")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
            生成调整预览
          </button>
        ) : (
          <div className="flex gap-3">
            <button onClick={() => setStep("edit")} className="w-[120px] shrink-0 py-4 bg-[#F7F9FC] text-gray-700 rounded-[24px] font-bold text-[15px] active:scale-[0.98] transition-transform border border-gray-100">
              返回修改
            </button>
            <button onClick={() => onGo("today")} className="flex-1 py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[15px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
              确认今天这样安排
            </button>
          </div>
        )}
      </div>
    </div>
  );
};export default function App() {
  const [page, setPage] = useState<Page>(initialPage);
  const [pendingReview, setPendingReview] = useState(false);
  const [navTab, setNavTab] = useState<NavTab>("today");
  const [selectedPositionId, setSelectedPositionId] = useState<string>("p1");

  const go = (p: Page) => {
    setPage(p);
    if (["today", "ability", "coach", "profile"].includes(p)) {
      setNavTab(p as NavTab);
    }
  };

  const back = () => {
    if (["practice", "photo", "essay", "placeholder", "result_summary", "task_basis", "weekly_plan", "today_adjustment", "daily_settlement", "essay_workbench"].includes(page)) setPage(navTab);
    else if (["result_review", "result_completed"].includes(page)) setPage("result_summary");
    else if (page === "positionDetail") setPage("position");
    else if (page === "position") setPage("profile");
    else if (page === "onboarding") setPage("login");
  };

  const nav = (tab: NavTab) => {
    setNavTab(tab);
    setPage(tab);
  };

  const mainPages: Page[] = ["today", "ability", "coach", "profile"];
  const showBottomNav = mainPages.includes(page);

  const renderPage = () => {
    switch (page) {
      case "login":
        return <LoginPage onLogin={() => go("onboarding")} />;
      case "onboarding":
        return <OnboardingPage onComplete={() => { go("today"); setNavTab("today"); }} />;
      case "today":
        return <TodayPage onGo={go} pendingReview={pendingReview} />;
      case "ability":
        return <AbilityTab onGo={go} />;
      case "coach":
        return <CoachTab onGo={go} />;
      case "profile":
        return <ProfileTab onGo={go} />;
      case "position":
        return <PositionPage onBack={back} onSelectPosition={(id) => { setSelectedPositionId(id); go("positionDetail"); }} />;
      case "positionDetail":
        return <PositionDetailPage posId={selectedPositionId} onBack={back} />;
      case "practice":
        return <PracticePage onGo={go} onBack={back} />;
      case "task_basis":
        return <TaskBasisPage onGo={go} onBack={back} />;
      case "weekly_plan":
        return <WeeklyPlanPage onGo={go} onBack={back} />;
      case "today_adjustment":
        return <TodayAdjustmentPage onGo={go} onBack={back} />;
      case "daily_settlement":
        return <DailySettlementPage onGo={go} onBack={back} />;
      case "onboarding_profile":
        return <OnboardingProfileWorkbenchPage onGo={go} onBack={back} />;
      case "photo_search":
        return <PhotoSearchWorkbenchPage onGo={go} onBack={back} />;
      case "library_workbench":
        return <LibraryWorkbenchPage onGo={go} onBack={back} />;
      case "position_workbench":
        return <PositionWorkbenchPage onGo={go} onBack={back} />;
      case "essay_workbench":
        return <EssayWorkbenchPage onGo={go} onBack={back} />;
      case "result_summary":
        return <ResultSummaryPage onGo={go} onBack={back} onLater={() => { setPendingReview(true); go("today"); }} />;
      case "result_review":
        return <ResultReviewPage onGo={go} onBack={back} />;
      case "result_completed":
        return <ResultCompletedPage onGo={go} onBack={back} />;
      case "photo":
        return <PhotoPage onBack={back} />;
      case "essay":
        return <EssayPage onBack={back} />;
      case "placeholder":
        return <PlaceholderPage onBack={back} />;
      default:
        return null;
    }
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4 sm:p-8"
      style={{
        backgroundColor: "#E5E7EB",
      }}
    >
      {/* Phone bezel */}
      <div
        id="figma-capture-root"
        className="relative flex-shrink-0"
        style={{
          width: 406,
          height: 860,
          background: "#111827",
          borderRadius: 56,
          boxShadow:
            "0 0 0 1px rgba(255,255,255,0.1), 0 60px 120px rgba(0,0,0,0.2), inset 0 0 0 0.5px rgba(255,255,255,0.1)",
          padding: 8,
        }}
      >
        {/* Screen */}
        <div
          className="w-full h-full overflow-hidden flex flex-col relative"
          style={{
            borderRadius: 48,
            backgroundColor: "#F7F9FC",
          }}
        >
          {/* Dynamic island / notch */}
          <div
            className="absolute top-3 left-1/2 -translate-x-1/2 z-[100]"
            style={{
              width: 120,
              height: 34,
              background: "#000",
              borderRadius: 20,
            }}
          />

          {/* Page content with transition */}
          <AnimatePresence mode="wait">
            <motion.div
              key={page}
              className="flex-1 flex flex-col overflow-hidden relative z-0"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 1.02 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
            >
              {renderPage()}
            </motion.div>
          </AnimatePresence>

          {/* Bottom nav */}
          {showBottomNav && <BottomNav active={navTab} onNav={nav} />}
        </div>
      </div>
    </div>
  );
}
