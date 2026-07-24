import React, { useState } from "react";
import { 
  ChevronLeft, Search, Sparkles, Plus, 
  FileText, Camera, CheckCircle2, Clock, Lock,
  Trash2, Edit3, X, Image as ImageIcon, ChevronRight, 
  Link2, AlertTriangle, FileUp, RefreshCw, BookOpen, PenTool
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { getCaptureState } from "../../capture-state";

type ViewState = "home" | "processing" | "detail" | "chat_citation";
const viewStates: readonly ViewState[] = ["home", "processing", "detail", "chat_citation"];

export const LibraryWorkbenchPage = ({ onGo, onBack }: { onGo: (page: string) => void; onBack: () => void }) => {
  const [view, setView] = useState<ViewState>(() => getCaptureState(viewStates, "home"));
  const [deleteDrawer, setDeleteDrawer] = useState(false);
  const [citationDrawer, setCitationDrawer] = useState(false);
  const [profileEnabled, setProfileEnabled] = useState(true);

  // A tiny floating pill to switch views easily for previewing purposes
  const ViewSwitcher = () => (
    <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[999] flex gap-1 bg-gray-900/90 backdrop-blur-md p-1.5 rounded-full shadow-2xl">
      {[
        { id: "home", label: "资料库首页" },
        { id: "processing", label: "上传与阅读" },
        { id: "detail", label: "资料管理" },
        { id: "chat_citation", label: "辅导溯源" },
      ].map((v) => (
        <button
          key={v.id}
          onClick={() => setView(v.id as ViewState)}
          className={`px-3 py-1.5 rounded-full text-[12px] font-bold transition-all ${
            view === v.id ? "bg-white text-gray-900" : "text-white/70 hover:text-white"
          }`}
        >
          {v.label}
        </button>
      ))}
    </div>
  );

  // -------------------------------------------------------------------------
  // 1. HOME VIEW (资料库首页)
  // -------------------------------------------------------------------------
  if (view === "home") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <ViewSwitcher />
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px]">
          <button onClick={onBack} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
          <span className="text-[16px] font-bold text-gray-900">专属资料库</span>
          <div className="w-8" />
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-2 pb-32 no-scrollbar">
          {/* Header Context */}
          <div className="mb-6 mt-2">
            <h1 className="text-[26px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-3">岸岸的私有阅读室</h1>
            <p className="text-[14px] text-gray-500 leading-relaxed font-medium">你上传的笔记、讲义和错题卷，只用于提升你自己的学习画像和答疑。不与其他考生共享，不训练公共模型。</p>
          </div>

          <div className="flex items-center gap-1.5 bg-gray-200/40 text-gray-500 px-3 py-2 rounded-xl mb-6 font-medium text-[12px]">
             <Lock size={12} className="text-gray-400" />
             <span>全程端到端加密，随时可彻底移除</span>
          </div>

          {/* Quick Actions */}
          <div className="flex gap-3 mb-8">
            <button onClick={() => setView("processing")} className="flex-1 bg-white rounded-[20px] p-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 active:scale-95 transition-transform flex flex-col items-center justify-center gap-2">
               <div className="w-10 h-10 rounded-full bg-indigo-50 flex items-center justify-center">
                 <FileUp size={20} className="text-indigo-500" />
               </div>
               <span className="text-[14px] font-bold text-gray-900">上传讲义/文档</span>
            </button>
            <button onClick={() => setView("processing")} className="flex-1 bg-white rounded-[20px] p-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 active:scale-95 transition-transform flex flex-col items-center justify-center gap-2">
               <div className="w-10 h-10 rounded-full bg-cyan-50 flex items-center justify-center">
                 <Camera size={20} className="text-cyan-600" />
               </div>
               <span className="text-[14px] font-bold text-gray-900">拍手写笔记</span>
            </button>
          </div>

          {/* Search */}
          <div className="relative mb-6">
            <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" />
            <input 
              type="text" 
              placeholder="搜索已理解的资料内容..." 
              className="w-full h-12 pl-11 pr-4 bg-white border border-gray-100 rounded-[16px] text-[14px] font-medium shadow-[0_2px_10px_rgba(0,0,0,0.01)] focus:outline-none focus:border-indigo-100"
            />
          </div>

          {/* Material List */}
          <div className="flex flex-col gap-5">
            <div>
              <div className="flex items-center justify-between mb-3 ml-1">
                 <h2 className="text-[16px] font-bold text-gray-900">岸岸正在阅读</h2>
                 <span className="text-[12px] font-bold text-indigo-500">1 份</span>
              </div>
              <div onClick={() => setView("processing")} className="bg-white rounded-[20px] p-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-indigo-50/50 flex items-start gap-3 active:scale-95 transition-transform">
                 <div className="w-10 h-10 rounded-[12px] bg-[#F7F9FC] flex items-center justify-center shrink-0">
                   <ImageIcon size={18} className="text-gray-400" />
                 </div>
                 <div className="flex-1">
                   <p className="text-[14px] font-bold text-gray-900 mb-1 truncate">黑板板书照片_0912.jpg</p>
                   <div className="flex items-center gap-1.5">
                     <RefreshCw size={12} className="text-indigo-400 animate-spin" />
                     <p className="text-[12px] font-medium text-indigo-500">正在提取手写考点...</p>
                   </div>
                 </div>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-3 ml-1">
                 <h2 className="text-[16px] font-bold text-gray-900">随时可引用 (12)</h2>
              </div>
              <div className="flex flex-col gap-3">
                 <div onClick={() => setView("detail")} className="bg-white rounded-[20px] p-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex items-start gap-3 active:scale-95 transition-transform">
                   <div className="w-10 h-10 rounded-[12px] bg-indigo-50 flex items-center justify-center shrink-0">
                     <BookOpen size={18} className="text-indigo-500" />
                   </div>
                   <div className="flex-1">
                     <div className="flex justify-between items-start mb-1">
                        <p className="text-[14px] font-bold text-gray-900 truncate pr-2">2024言语理解高频词汇讲义.pdf</p>
                        <span className="shrink-0 bg-green-50 text-green-600 px-1.5 py-0.5 rounded text-[10px] font-bold mt-0.5">已掌握</span>
                     </div>
                     <p className="text-[12px] text-gray-400 font-medium mb-2">已与你的错题库关联 · 45页</p>
                     <div className="flex items-center gap-1">
                       <CheckCircle2 size={12} className="text-gray-300" />
                       <span className="text-[11px] font-medium text-gray-400">计入能力画像</span>
                     </div>
                   </div>
                 </div>

                 <div onClick={() => setView("detail")} className="bg-white rounded-[20px] p-4 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex items-start gap-3 active:scale-95 transition-transform">
                   <div className="w-10 h-10 rounded-[12px] bg-orange-50 flex items-center justify-center shrink-0">
                     <FileText size={18} className="text-orange-500" />
                   </div>
                   <div className="flex-1">
                     <div className="flex justify-between items-start mb-1">
                        <p className="text-[14px] font-bold text-gray-900 truncate pr-2">粉笔模考(三)申论答题纸扫描.jpg</p>
                        <span className="shrink-0 bg-green-50 text-green-600 px-1.5 py-0.5 rounded text-[10px] font-bold mt-0.5">已掌握</span>
                     </div>
                     <p className="text-[12px] text-gray-400 font-medium mb-2">已提取手写内容和分数</p>
                     <div className="flex items-center gap-1">
                       <Lock size={12} className="text-gray-300" />
                       <span className="text-[11px] font-medium text-gray-400">仅供检索，不计入画像</span>
                     </div>
                   </div>
                 </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 2. PROCESSING VIEW (上传与阅读)
  // -------------------------------------------------------------------------
  if (view === "processing") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans overflow-hidden">
        <ViewSwitcher />
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="h-14 flex items-center px-4 shrink-0 z-10 mt-[48px]">
          <button onClick={() => setView("home")} className="p-2 -ml-2 active:scale-95 text-gray-900"><X size={24} /></button>
        </div>

        <div className="flex-1 px-8 flex flex-col justify-center items-center pb-20">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="flex flex-col items-center w-full max-w-[300px]"
          >
            <div className="relative mb-8">
              <div className="w-24 h-24 bg-white rounded-[24px] flex items-center justify-center shadow-[0_8px_30px_rgba(0,0,0,0.06)] border border-gray-50 z-10 relative">
                <FileText size={40} className="text-indigo-500" />
              </div>
              {/* Fake scanning beam */}
              <motion.div 
                animate={{ top: ["0%", "100%", "0%"] }}
                transition={{ duration: 2.5, ease: "linear", repeat: Infinity }}
                className="absolute left-0 right-0 h-1 bg-cyan-400 shadow-[0_0_15px_rgba(34,211,238,0.6)] z-20 rounded-full"
              />
            </div>

            <h2 className="text-[20px] font-bold text-gray-900 tracking-tight mb-2">岸岸正在阅读新资料</h2>
            <p className="text-[13px] font-bold text-indigo-500 mb-8">2024言语理解高频词汇讲义.pdf</p>

            <div className="w-full flex flex-col gap-4 text-left">
              <div className="flex items-center gap-3">
                <CheckCircle2 size={18} className="text-green-500" />
                <span className="text-[14px] font-bold text-gray-900">扫描文字与图表结构</span>
              </div>
              <div className="flex items-center gap-3">
                <RefreshCw size={18} className="text-indigo-500 animate-spin" />
                <span className="text-[14px] font-bold text-gray-900">提取并理解核心考点</span>
              </div>
              <div className="flex items-center gap-3 opacity-40">
                <div className="w-4 h-4 rounded-full border-2 border-gray-300 ml-0.5" />
                <span className="text-[14px] font-bold text-gray-900 ml-0.5">与你的错题记录建立关联</span>
              </div>
            </div>

            <div className="mt-12 bg-indigo-50/50 p-4 rounded-[20px] border border-indigo-50">
              <p className="text-[12px] text-gray-500 font-medium leading-relaxed text-center">
                理解这份讲义大约需要 30 秒。你可以先去刷题，完成后岸岸会通知你。
              </p>
            </div>
            
            <button onClick={() => setView("home")} className="mt-8 text-[14px] font-bold text-gray-500 active:scale-95 transition-transform py-2">
              先去后台运行
            </button>
          </motion.div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 3. DETAIL & MANAGEMENT VIEW (资料管理)
  // -------------------------------------------------------------------------
  if (view === "detail") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <ViewSwitcher />
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px] bg-[#F7F9FC]/80 backdrop-blur-xl">
          <button onClick={() => setView("home")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
          <button className="p-2 -mr-2 active:scale-95 text-gray-900"><Edit3 size={18} /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-2 pb-32 no-scrollbar">
          {/* Header */}
          <div className="mb-6 flex flex-col items-center text-center">
            <div className="w-16 h-16 bg-white rounded-[20px] flex items-center justify-center shadow-[0_4px_15px_rgba(0,0,0,0.03)] border border-gray-50 mb-4">
              <BookOpen size={28} className="text-indigo-500" />
            </div>
            <h1 className="text-[20px] font-bold text-gray-900 tracking-tight leading-[1.3] mb-2">2024言语理解高频词汇讲义</h1>
            <p className="text-[13px] font-medium text-gray-400">上传于 2024-03-12 · 45页 PDF</p>
          </div>

          {/* Status Banner */}
          <div className="bg-gradient-to-br from-green-50 to-emerald-50 rounded-[24px] p-5 border border-green-100/50 mb-6 flex items-start gap-3 relative overflow-hidden">
             <div className="absolute top-0 right-0 w-24 h-24 bg-green-200/30 rounded-full blur-2xl -translate-y-1/2 translate-x-1/3" />
             <CheckCircle2 size={20} className="text-green-600 mt-0.5 shrink-0 relative z-10" />
             <div className="relative z-10">
               <h3 className="text-[15px] font-bold text-green-900 mb-1">岸岸已完全掌握此内容</h3>
               <p className="text-[13px] text-green-800/80 font-medium leading-relaxed">
                 随时可用于为你生成讲解、错题溯源和检索。
               </p>
             </div>
          </div>

          {/* Insight / Understanding */}
          <h2 className="text-[16px] font-bold text-gray-900 mb-3 ml-1">岸岸从中读到了什么</h2>
          <div className="bg-white rounded-[24px] p-5 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6 flex flex-col gap-4">
            <div className="flex items-start gap-3">
              <Sparkles size={16} className="text-indigo-500 mt-0.5 shrink-0" />
              <p className="text-[14px] text-gray-700 font-medium leading-relaxed">
                包含 <span className="font-bold text-gray-900">120 个</span> 言语理解核心词义辨析考点。
              </p>
            </div>
            <div className="h-px bg-gray-50 ml-7" />
            <div className="flex items-start gap-3">
              <Link2 size={16} className="text-cyan-600 mt-0.5 shrink-0" />
              <p className="text-[14px] text-gray-700 font-medium leading-relaxed">
                已自动匹配你错题本中的 <span className="font-bold text-gray-900">14 道</span> 历史错题，补充了解题口诀。
              </p>
            </div>
          </div>

          {/* Settings */}
          <h2 className="text-[16px] font-bold text-gray-900 mb-3 ml-1">资料权限设置</h2>
          <div className="bg-white rounded-[24px] shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-6 flex flex-col overflow-hidden">
             
             <div className="p-5 flex items-center justify-between border-b border-gray-50">
               <div className="flex-1 pr-4">
                 <h4 className="text-[15px] font-bold text-gray-900 mb-1">允许岸岸主动引用</h4>
                 <p className="text-[12px] text-gray-400 font-medium leading-relaxed">在辅导答疑时，优先使用该讲义的口诀和解析方式。</p>
               </div>
               {/* Custom Toggle */}
               <div className="w-12 h-7 bg-green-500 rounded-full flex items-center p-1 shrink-0 justify-end transition-colors cursor-pointer">
                 <div className="w-5 h-5 bg-white rounded-full shadow-sm" />
               </div>
             </div>

             <div className="p-5 flex items-center justify-between">
               <div className="flex-1 pr-4">
                 <h4 className="text-[15px] font-bold text-gray-900 mb-1">计入个人能力画像</h4>
                 <p className="text-[12px] text-gray-400 font-medium leading-relaxed">开启后，岸岸将认为你已经系统学过该讲义包含的考点。</p>
               </div>
               <div 
                 onClick={() => setProfileEnabled(!profileEnabled)}
                 className={`w-12 h-7 rounded-full flex items-center p-1 shrink-0 transition-colors cursor-pointer ${profileEnabled ? "bg-green-500 justify-end" : "bg-gray-200 justify-start"}`}
               >
                 <div className="w-5 h-5 bg-white rounded-full shadow-sm" />
               </div>
             </div>
          </div>

          {/* Danger Zone */}
          <div className="flex flex-col gap-3">
             <button className="bg-white rounded-[20px] p-4 text-[14px] font-bold text-gray-700 shadow-[0_2px_10px_rgba(0,0,0,0.01)] border border-gray-50 active:scale-95 transition-transform flex items-center justify-center gap-2">
               上传新版本并替换
             </button>
             <button onClick={() => setDeleteDrawer(true)} className="bg-white rounded-[20px] p-4 text-[14px] font-bold text-red-500 shadow-[0_2px_10px_rgba(0,0,0,0.01)] border border-red-50 active:scale-95 transition-transform flex items-center justify-center gap-2">
               <Trash2 size={16} /> 从资料库彻底移除
             </button>
          </div>
        </div>

        {/* Delete Confirmation Drawer */}
        <AnimatePresence>
          {deleteDrawer && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 bg-gray-900/40 backdrop-blur-sm flex flex-col justify-end"
              onClick={() => setDeleteDrawer(false)}
            >
              <motion.div 
                initial={{ y: "100%" }}
                animate={{ y: 0 }}
                exit={{ y: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 200 }}
                className="bg-white rounded-t-[32px] p-6 pt-4 pb-safe"
                onClick={e => e.stopPropagation()}
              >
                <div className="w-12 h-1.5 bg-gray-200 rounded-full mx-auto mb-6" />
                <div className="flex flex-col items-center text-center mb-6">
                  <div className="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mb-4">
                    <AlertTriangle size={24} className="text-red-500" />
                  </div>
                  <h3 className="text-[20px] font-bold text-gray-900 tracking-tight mb-2">将该讲义移出资料库？</h3>
                  <p className="text-[14px] text-gray-500 font-medium leading-relaxed px-4">
                    彻底删除后，不保留任何备份，且无法恢复。
                  </p>
                </div>
                
                <div className="bg-[#F7F9FC] rounded-[24px] p-5 border border-gray-100 flex flex-col gap-3 mb-6">
                   <div className="flex items-start gap-2.5">
                     <div className="w-1.5 h-1.5 rounded-full bg-red-400 mt-2 shrink-0" />
                     <p className="text-[13px] text-gray-600 font-medium leading-relaxed">岸岸将彻底遗忘这份资料的所有内容。</p>
                   </div>
                   <div className="flex items-start gap-2.5">
                     <div className="w-1.5 h-1.5 rounded-full bg-red-400 mt-2 shrink-0" />
                     <p className="text-[13px] text-gray-600 font-medium leading-relaxed">关联的错题诊断，不再提供此讲义的溯源解析。</p>
                   </div>
                   <div className="flex items-start gap-2.5">
                     <div className="w-1.5 h-1.5 rounded-full bg-red-400 mt-2 shrink-0" />
                     <p className="text-[13px] text-gray-600 font-medium leading-relaxed">它对你『言语理解』能力画像的影响，将在今晚重新结算时被撤销。</p>
                   </div>
                </div>

                <div className="flex gap-3">
                  <button onClick={() => setDeleteDrawer(false)} className="flex-1 py-4 bg-[#F7F9FC] text-gray-700 rounded-[24px] font-bold text-[15px] active:scale-[0.98] transition-transform">
                    保留
                  </button>
                  <button onClick={() => { setDeleteDrawer(false); setView("home"); }} className="flex-1 py-4 bg-red-500 text-white rounded-[24px] font-bold text-[15px] shadow-[0_8px_20px_rgba(239,68,68,0.2)] active:scale-[0.98] transition-transform">
                    确认移除
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 4. CHAT CITATION VIEW (辅导溯源)
  // -------------------------------------------------------------------------
  if (view === "chat_citation") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans overflow-hidden">
        <ViewSwitcher />
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        <div className="h-14 bg-white/80 backdrop-blur-md border-b border-gray-100 flex items-center justify-between px-4 shrink-0 mt-[48px] z-20">
          <div className="flex items-center gap-3">
            <button onClick={() => setView("home")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
            <span className="text-[16px] font-bold text-gray-900">错题复盘解答</span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-6 pb-32 flex flex-col gap-6 no-scrollbar relative z-10">
           {/* Mock User Message */}
           <div className="flex justify-end mb-2">
             <div className="bg-gray-900 text-white p-4 rounded-[24px] rounded-tr-[8px] max-w-[85%] shadow-sm">
               <p className="text-[15px] font-medium leading-relaxed">这道题 A 和 C 意思很像，为什么不能选 A？</p>
             </div>
           </div>

           {/* Mock An'an Message */}
           <div className="flex items-start gap-3">
             <div className="w-8 h-8 rounded-[12px] bg-gradient-to-br from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center shrink-0 shadow-md shadow-purple-500/20 mt-1">
               <Sparkles size={14} className="text-white" />
             </div>
             <div className="flex-1 flex flex-col gap-3">
                <div className="bg-white p-4 rounded-[24px] rounded-tl-[8px] shadow-[0_2px_15px_rgba(0,0,0,0.03)] border border-gray-50">
                  <p className="text-[15px] text-gray-800 leading-[1.8] font-medium mb-3">
                    确实很容易混淆。但其实根据你上周上传的专属讲义里的口诀，我们可以快速排除 A 选项。
                  </p>
                  <p className="text-[15px] text-gray-800 leading-[1.8] font-medium">
                    A 选项属于“强加因果”，而原题中两个词处于并列结构，不构成递进。
                  </p>
                </div>
                
                {/* Citation Card */}
                <div 
                  onClick={() => setCitationDrawer(true)}
                  className="bg-indigo-50/50 p-4 rounded-[20px] border border-indigo-100/50 flex flex-col gap-3 active:scale-[0.98] transition-transform cursor-pointer"
                >
                   <div className="flex items-center gap-2">
                     <BookOpen size={14} className="text-indigo-500" />
                     <span className="text-[12px] font-bold text-indigo-700">引用于你的专属资料</span>
                   </div>
                   <div>
                     <p className="text-[14px] font-bold text-gray-900 mb-1">《2024言语理解高频词汇讲义》</p>
                     <p className="text-[12px] text-gray-500 font-medium">第 14 页 · “强加因果的典型标志词”</p>
                   </div>
                   <div className="mt-1 flex items-center gap-1 text-[12px] font-bold text-indigo-500">
                     点击查看原内容快照 <ChevronRight size={14} />
                   </div>
                </div>
             </div>
           </div>
        </div>

        {/* Bottom Input Mock */}
        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-4 pb-safe z-30">
          <div className="flex gap-2">
            <div className="h-12 flex-1 bg-[#F7F9FC] border border-gray-100 rounded-full flex items-center px-4">
              <span className="text-[14px] text-gray-400 font-medium">继续问岸岸...</span>
            </div>
            <div className="w-12 h-12 bg-gray-900 rounded-full flex items-center justify-center shrink-0">
               <ChevronRight size={20} className="text-white" />
            </div>
          </div>
        </div>

        {/* Citation Source Drawer */}
        <AnimatePresence>
          {citationDrawer && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 bg-gray-900/40 backdrop-blur-sm flex flex-col justify-end"
              onClick={() => setCitationDrawer(false)}
            >
              <motion.div 
                initial={{ y: "100%" }}
                animate={{ y: 0 }}
                exit={{ y: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 200 }}
                className="bg-white rounded-t-[32px] pt-4 pb-safe flex flex-col h-[75vh]"
                onClick={e => e.stopPropagation()}
              >
                <div className="w-12 h-1.5 bg-gray-200 rounded-full mx-auto mb-5 shrink-0" />
                <div className="flex justify-between items-center px-6 mb-4 shrink-0">
                  <div>
                    <h3 className="text-[18px] font-bold text-gray-900 tracking-tight mb-1">原文快照</h3>
                    <p className="text-[12px] text-gray-500 font-medium">来源：2024言语理解高频词汇讲义 第 14 页</p>
                  </div>
                  <button onClick={() => setCitationDrawer(false)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center active:scale-95">
                    <X size={16} className="text-gray-500" />
                  </button>
                </div>

                <div className="flex-1 overflow-y-auto px-6 pb-8">
                   <div className="bg-[#F7F9FC] rounded-[24px] p-6 border border-gray-100 relative shadow-inner">
                      {/* Fake PDF rendering style */}
                      <p className="text-[18px] font-bold text-gray-900 mb-6 text-center border-b border-gray-200 pb-4">第三节 逻辑填空易错点</p>
                      
                      <p className="text-[15px] leading-[2] text-gray-700 mb-4 indent-8">
                        在逻辑填空中，很多选项会在关联词上做文章。考生最容易犯的错误就是忽略了前后文的真实逻辑关系。
                      </p>
                      
                      {/* Highlighted section */}
                      <div className="relative">
                        <div className="absolute -inset-x-3 -inset-y-2 bg-indigo-100/50 border-l-4 border-indigo-400 rounded-r-lg z-0" />
                        <p className="text-[15px] leading-[2] text-gray-900 font-bold relative z-10">
                          特别注意“强加因果”的陷阱：当两个词语或短句在原文中明明是并列结构（如：不仅...而且，既...又），选项如果使用“因为...所以”或者“导致了”，就是典型的强加因果，直接排除。
                        </p>
                      </div>

                      <p className="text-[15px] leading-[2] text-gray-700 mt-6 indent-8">
                        口诀总结：并列不因果，转折不递进。遇到长难句，先看标点符。
                      </p>
                   </div>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    );
  }

  return null;
};
