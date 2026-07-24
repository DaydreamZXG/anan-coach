import React, { useState, useEffect } from "react";
import { 
  ChevronLeft, Camera, Image as ImageIcon, Zap, Maximize, 
  RotateCcw, CheckCircle2, AlertTriangle, ChevronRight, 
  Search, BookOpen, PenTool, Edit3, Type, ArrowRight, List, Sparkles,
  RefreshCw, MessageSquare, AlertCircle, Bookmark, FileText, X,
  AlignJustify, Settings2, Trash2, ShieldAlert, Brain, Undo2
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { getCaptureState } from "../../capture-state";

type ViewState = 
  | "no_permission"
  | "camera" 
  | "crop" 
  | "ocr" 
  | "searching" 
  | "multi_hit" 
  | "no_hit"
  | "searching_ai"
  | "solution_bank" 
  | "solution_ai"
  | "record_success";
const viewStates: readonly ViewState[] = [
  "no_permission", "camera", "crop", "ocr", "searching", "multi_hit",
  "no_hit", "searching_ai", "solution_bank", "solution_ai", "record_success",
];

export const PhotoSearchWorkbenchPage = ({ onGo, onBack }: { onGo: (page: string) => void; onBack: () => void }) => {
  // Initialization & States
  const [view, setView] = useState<ViewState>(() => getCaptureState(viewStates, "no_permission"));
  const [mode, setMode] = useState<"single" | "material">("single");
  const [materialStep, setMaterialStep] = useState<number>(0); // 0: unstarted, 1: captured material, 2: capturing question
  
  // Camera tools
  const [flash, setFlash] = useState(false);
  const [cameraWarning, setCameraWarning] = useState<string | null>(null);
  
  // Crop tools
  const [rotation, setRotation] = useState(0);
  const [perspective, setPerspective] = useState(false);

  // OCR
  const [subject, setSubject] = useState<"xingce" | "shenlun">("xingce");
  
  // Recording
  const [recordDrawer, setRecordDrawer] = useState(false);
  const [recordType, setRecordType] = useState<"view" | "mistake" | "real" | null>(null);
  const [userAnswer, setUserAnswer] = useState<string | null>(null);
  const [timeSpent, setTimeSpent] = useState("");
  const [confidence, setConfidence] = useState("正常");

  // Mock Question Data
  const mockQ = {
    source: "演示题",
    text: "2022年，某市城镇居民人均可支配收入为49283元，农村居民人均可支配收入为20133元。城镇常住人口与农村常住人口比例为 2:1。求全市居民人均可支配收入约为多少元？",
    options: ["A. 33000", "B. 36883", "C. 39566", "D. 42000"],
    correct: "C",
    calc: "(49283 × 2 + 20133 × 1) ÷ 3 = 118699 ÷ 3 ≈ 39566.33"
  };

  // Simulate camera focus/warning
  useEffect(() => {
    if (view === "camera") {
      const timer = setTimeout(() => {
        setCameraWarning("光线较暗，建议开启闪光灯");
      }, 2000);
      return () => clearTimeout(timer);
    }
  }, [view]);

  // -------------------------------------------------------------------------
  // 0. NO PERMISSION
  // -------------------------------------------------------------------------
  if (view === "no_permission") {
    return (
      <div className="flex flex-col h-full bg-black relative font-sans items-center justify-center text-center px-8">
        <div className="w-20 h-20 bg-gray-900 rounded-full flex items-center justify-center mb-6">
          <Camera size={32} className="text-gray-400" />
        </div>
        <h2 className="text-[20px] font-bold text-white mb-3">需要相机权限</h2>
        <p className="text-[14px] text-gray-400 font-medium mb-10 leading-relaxed">
          岸岸需要使用相机来拍摄题目和材料，请在系统弹窗中允许授权。
        </p>
        <button onClick={() => setView("camera")} className="w-full py-4 bg-cyan-500 text-white rounded-[24px] font-bold text-[16px] active:scale-95 transition-transform">
          允许授权
        </button>
        <button onClick={onBack} className="mt-6 text-[14px] font-bold text-gray-500 active:scale-95">返回</button>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 1. CAMERA VIEW (拍摄与导入)
  // -------------------------------------------------------------------------
  if (view === "camera") {
    return (
      <div className="flex flex-col h-full bg-black relative font-sans overflow-hidden">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        {/* Top Actions */}
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10">
          <button onClick={onBack} className="p-2 -ml-2 active:scale-95 text-white"><ChevronLeft size={24} /></button>
          
          <div className="flex bg-gray-900/80 backdrop-blur-md rounded-full p-1 border border-gray-700">
            <button 
              onClick={() => { setMode("single"); setMaterialStep(0); }} 
              className={`px-4 py-1.5 rounded-full text-[13px] font-bold transition-colors ${mode === "single" ? "bg-gray-700 text-white shadow-sm" : "text-gray-400"}`}
            >
              单题
            </button>
            <button 
              onClick={() => setMode("material")} 
              className={`px-4 py-1.5 rounded-full text-[13px] font-bold transition-colors ${mode === "material" ? "bg-gray-700 text-white shadow-sm" : "text-gray-400"}`}
            >
              共享材料
            </button>
          </div>
          
          <button onClick={() => setFlash(!flash)} className={`p-2 -mr-2 active:scale-95 ${flash ? "text-yellow-400" : "text-white"}`}>
            <Zap size={20} className={flash ? "fill-yellow-400" : ""} />
          </button>
        </div>

        <div className="flex-1 relative flex flex-col justify-center items-center">
           {/* Viewfinder Frame */}
           <div className={`w-[85%] aspect-[3/4] border transition-colors duration-300 rounded-[24px] relative flex flex-col justify-center items-center ${mode === "material" && materialStep === 0 ? "border-cyan-400/50 bg-cyan-900/10" : "border-white/30"}`}>
              <div className="absolute top-4 left-4 w-6 h-6 border-t-2 border-l-2 border-white rounded-tl-[8px]" />
              <div className="absolute top-4 right-4 w-6 h-6 border-t-2 border-r-2 border-white rounded-tr-[8px]" />
              <div className="absolute bottom-4 left-4 w-6 h-6 border-b-2 border-l-2 border-white rounded-bl-[8px]" />
              <div className="absolute bottom-4 right-4 w-6 h-6 border-b-2 border-r-2 border-white rounded-br-[8px]" />
              
              <div className="text-center px-6">
                <p className="text-white/80 text-[15px] font-bold tracking-widest mb-2 shadow-black drop-shadow-md">
                  {mode === "single" ? "请将题干与选项完整对齐" : (materialStep === 0 ? "请先拍摄整段共享材料" : "请拍摄关联的具体题目")}
                </p>
                {mode === "material" && materialStep === 0 && (
                  <p className="text-cyan-300/80 text-[12px] font-medium shadow-black drop-shadow-md">支持连续拍摄多页材料</p>
                )}
              </div>
           </div>

           {/* Camera Warnings */}
           <AnimatePresence>
             {cameraWarning && (
               <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="absolute bottom-10 bg-yellow-500/20 border border-yellow-500/50 backdrop-blur-md px-4 py-2 rounded-full flex items-center gap-2">
                 <AlertTriangle size={14} className="text-yellow-400" />
                 <span className="text-yellow-400 text-[12px] font-bold">{cameraWarning}</span>
               </motion.div>
             )}
           </AnimatePresence>
        </div>

        {/* Material Strip Indicator */}
        {mode === "material" && materialStep > 0 && (
          <div className="h-16 bg-gray-900 flex items-center px-6 gap-3 shrink-0 border-t border-gray-800">
             <div className="w-10 h-10 rounded-lg bg-gray-800 border border-gray-700 flex items-center justify-center shrink-0">
               <FileText size={16} className="text-cyan-400" />
             </div>
             <div>
               <p className="text-white text-[13px] font-bold">已拍 1 张材料</p>
               <p className="text-gray-400 text-[11px] font-medium">可继续补拍材料，或拍摄关联题目</p>
             </div>
             <button onClick={() => setMaterialStep(2)} className="ml-auto bg-cyan-500 text-white px-3 py-1.5 rounded-full text-[12px] font-bold active:scale-95">拍题目</button>
          </div>
        )}

        {/* Bottom Controls */}
        <div className="h-[140px] bg-black shrink-0 flex items-center justify-around px-8 pb-safe">
           <button onClick={() => setView("crop")} className="w-12 h-12 rounded-full bg-gray-900 flex items-center justify-center border border-gray-800 active:scale-95">
             <ImageIcon size={20} className="text-white" />
           </button>
           <button 
             onClick={() => {
               if (mode === "single") setView("crop");
               else if (materialStep === 0) setMaterialStep(1);
               else setView("crop");
             }} 
             className="w-[72px] h-[72px] rounded-full border-4 border-white flex items-center justify-center active:scale-95 transition-transform p-1"
           >
             <div className="w-full h-full bg-white rounded-full" />
           </button>
           <div className="w-12 h-12" /> {/* Spacer */}
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 2. CROP & ADJUST VIEW (裁剪透视)
  // -------------------------------------------------------------------------
  if (view === "crop") {
    return (
      <div className="flex flex-col h-full bg-black relative font-sans overflow-hidden">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10">
          <button onClick={() => setView("camera")} className="p-2 -ml-2 active:scale-95 text-white"><ChevronLeft size={24} /></button>
          <span className="text-white font-bold text-[16px]">{mode === "material" ? "框选内容并核对" : "框选题目区域"}</span>
          <div className="w-8" />
        </div>

        <div className="flex-1 relative flex items-center justify-center p-6">
           <motion.div 
             animate={{ rotate: rotation, perspective: perspective ? "800px" : "none", rotateX: perspective ? 10 : 0 }} 
             className="w-full h-[75%] bg-gradient-to-br from-gray-800 to-gray-900 rounded-[12px] relative flex items-center justify-center border border-gray-700"
           >
              <div className="text-center">
                 <FileText size={48} className="text-gray-700 mx-auto mb-2 opacity-50" />
                 <p className="text-gray-500 text-[12px] font-bold">已捕获图像</p>
              </div>
              
              {/* Draggable Crop Overlay Mock */}
              <div className="absolute inset-8 border-2 border-cyan-400 bg-cyan-400/10 shadow-[0_0_0_9999px_rgba(0,0,0,0.7)] flex flex-col justify-between">
                 <div className="absolute -top-2 -left-2 w-4 h-4 bg-white border-2 border-cyan-400 rounded-full" />
                 <div className="absolute -top-2 -right-2 w-4 h-4 bg-white border-2 border-cyan-400 rounded-full" />
                 <div className="absolute -bottom-2 -left-2 w-4 h-4 bg-white border-2 border-cyan-400 rounded-full" />
                 <div className="absolute -bottom-2 -right-2 w-4 h-4 bg-white border-2 border-cyan-400 rounded-full" />
                 
                 {mode === "material" && (
                   <div className="absolute top-2 left-1/2 -translate-x-1/2 bg-cyan-500 text-white text-[10px] font-bold px-2 py-0.5 rounded shadow-sm">
                     材料区域
                   </div>
                 )}
              </div>
           </motion.div>
        </div>

        <div className="h-24 bg-gray-900 shrink-0 flex items-center justify-between px-6 pb-safe border-t border-gray-800">
           <button onClick={() => setView("camera")} className="text-white font-bold text-[14px] px-2 active:scale-95">重拍</button>
           <div className="flex gap-5">
             <button onClick={() => setRotation(rotation - 90)} className="w-10 h-10 rounded-full bg-gray-800 flex items-center justify-center text-white active:scale-95"><RotateCcw size={18} /></button>
             <button onClick={() => setPerspective(!perspective)} className={`w-10 h-10 rounded-full flex items-center justify-center active:scale-95 transition-colors ${perspective ? "bg-cyan-500 text-white" : "bg-gray-800 text-white"}`}><Maximize size={18} /></button>
           </div>
           <button onClick={() => setView("ocr")} className="bg-cyan-500 text-white font-bold text-[14px] px-6 py-2.5 rounded-full active:scale-95 shadow-[0_4px_15px_rgba(6,182,212,0.3)]">确认</button>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 3. OCR CONFIRMATION VIEW (文字确认)
  // -------------------------------------------------------------------------
  if (view === "ocr") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans overflow-hidden">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        <div className="h-14 flex items-center justify-between px-4 shrink-0 bg-white border-b border-gray-100 z-10 shadow-sm">
          <button onClick={() => setView("crop")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
          <span className="text-gray-900 font-bold text-[16px]">核对识别内容</span>
          <div className="w-8" />
        </div>

        <div className="flex-1 flex flex-col relative overflow-hidden">
           {/* Top: Original Image Reference (Sticky) */}
           <div className="h-[25%] bg-gray-900 relative shrink-0 overflow-hidden flex items-center justify-center border-b border-gray-200">
             <FileText size={48} className="text-gray-700" />
             <div className="absolute bottom-3 right-3 bg-black/60 backdrop-blur-md px-3 py-1.5 rounded-lg text-white text-[12px] font-bold flex items-center gap-1.5 active:scale-95 cursor-pointer">
               <ImageIcon size={14} /> 对照原图
             </div>
           </div>

           {/* Bottom: Editable Text Areas */}
           <div className="flex-1 overflow-y-auto px-5 pt-5 pb-[120px] no-scrollbar bg-[#F7F9FC]">
              
              {/* Subject Toggle */}
              <div className="flex justify-between items-center mb-5 bg-white p-3 rounded-[20px] shadow-[0_2px_10px_rgba(0,0,0,0.02)] border border-gray-50">
                 <div className="flex items-center gap-2">
                   <div className="bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded text-[12px] font-bold">自动识别</div>
                   <span className="text-[14px] font-bold text-gray-900">行测考题</span>
                 </div>
                 <button 
                   onClick={() => {
                     // Confirm flow for Shenlun
                     if (window.confirm("申论作答与解析体验不同，将为您切换至申论专属工作台。")) {
                       onGo("essay_workbench");
                     }
                   }} 
                   className="text-[12px] font-bold text-gray-500 bg-gray-100 px-3 py-1.5 rounded-full hover:text-gray-900 transition-colors"
                 >
                   改为申论
                 </button>
              </div>

              {/* Material Section (If mode == material) */}
              {mode === "material" && (
                <div className="bg-white rounded-[24px] shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex flex-col overflow-hidden mb-6 relative">
                  <div className="absolute left-0 top-0 bottom-0 w-1 bg-cyan-400" />
                  <div className="bg-gray-50 px-5 py-3 border-b border-gray-100 flex justify-between items-center">
                    <span className="text-[14px] font-bold text-gray-700 flex items-center gap-1.5"><FileText size={16}/> 共享材料片段</span>
                  </div>
                  <div className="p-5">
                    <textarea 
                      className="w-full min-h-[80px] bg-transparent text-[14px] leading-[1.8] text-gray-800 font-medium focus:outline-none resize-none"
                      defaultValue="2022年，某市城镇居民人均可支配收入为49283元，农村居民人均可支配收入为20133元。城镇常住人口与农村常住人口比例为 2:1。"
                    />
                  </div>
                </div>
              )}

              {/* Question Section */}
              <div className="bg-white rounded-[24px] shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex flex-col overflow-hidden mb-6">
                <div className="bg-gray-50 px-5 py-3 border-b border-gray-100 flex justify-between items-center">
                  <span className="text-[14px] font-bold text-gray-700 flex items-center gap-1.5"><Type size={16}/> 题干</span>
                </div>
                <div className="p-5">
                  <p className="text-[15px] leading-[1.8] text-gray-800 focus:outline-none" contentEditable suppressContentEditableWarning>
                    求全市居民人均可支配收入约为多少<span className="bg-orange-100 text-orange-700 px-1 rounded mx-0.5 border-b border-orange-300">园</span>？
                  </p>
                  <div className="flex items-center gap-1.5 mt-4 pt-3 border-t border-gray-50">
                    <AlertTriangle size={14} className="text-orange-500" />
                    <span className="text-[12px] font-bold text-gray-500">1 处疑似识别错误，请直接点击修改</span>
                  </div>
                </div>
              </div>

              {/* Options Section */}
              <div className="bg-white rounded-[24px] shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex flex-col overflow-hidden mb-6 relative">
                <div className="bg-gray-50 px-5 py-3 border-b border-gray-100 flex justify-between items-center">
                  <span className="text-[14px] font-bold text-gray-700 flex items-center gap-1.5"><List size={16}/> 选项</span>
                </div>
                <div className="p-5 flex flex-col gap-3">
                  {mockQ.options.map((opt, i) => (
                    <div key={i} className="flex gap-2">
                       <input type="text" defaultValue={opt} className="flex-1 bg-[#F7F9FC] border border-gray-100 rounded-[12px] px-4 py-3 text-[14px] text-gray-800 font-bold focus:outline-none focus:border-indigo-300 focus:bg-white transition-colors shadow-sm" />
                    </div>
                  ))}
                </div>
                
                {/* Intentional Warning for demo completeness */}
                <div className="bg-red-50/50 p-4 border-t border-red-100 flex items-start gap-2">
                  <ShieldAlert size={16} className="text-red-500 shrink-0 mt-0.5" />
                  <p className="text-[12px] font-bold text-red-700/80 leading-relaxed">
                    选项内容过短或疑似缺失，若不完整可能导致匹配失败，请补充。
                  </p>
                </div>
              </div>
           </div>

           <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
              <button onClick={() => { setView("searching"); setTimeout(() => setView("multi_hit"), 1500); }} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform flex justify-center items-center gap-2">
                确认无误，开始搜索 <ArrowRight size={18} />
              </button>
           </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 4. SEARCHING
  // -------------------------------------------------------------------------
  if (view === "searching" || view === "searching_ai") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans overflow-hidden items-center justify-center">
        <motion.div 
          animate={{ rotate: 360 }} 
          transition={{ duration: 2, ease: "linear", repeat: Infinity }}
          className={`w-16 h-16 border-4 rounded-full mb-6 ${view === "searching" ? "border-indigo-100 border-t-indigo-500" : "border-cyan-100 border-t-cyan-500"}`}
        />
        <h2 className="text-[20px] font-bold text-gray-900 tracking-tight mb-2">
          {view === "searching" ? "正在全库检索..." : "AI 正在独立推理..."}
        </h2>
        <p className="text-[14px] text-gray-500 font-medium">
          {view === "searching" ? "寻找最精准的原题与解析" : "由于题库未命中，正在启动多模型联合计算"}
        </p>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 5. MULTI HIT (多个相似题)
  // -------------------------------------------------------------------------
  if (view === "multi_hit") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        <div className="h-14 flex items-center px-4 shrink-0 z-10 mt-[48px] bg-[#F7F9FC]/80 backdrop-blur-xl">
          <button onClick={() => setView("ocr")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-2 pb-32 no-scrollbar">
          <h1 className="text-[28px] font-bold text-gray-900 tracking-tight mb-3">找到相似题目</h1>
          <p className="text-[14px] text-gray-500 font-medium leading-relaxed mb-6">题库中存在多道高度相似的考题，请确认你要找的是哪一道：</p>

          <div className="flex flex-col gap-4">
             {/* Hit 1 */}
             <div onClick={() => setView("solution_bank")} className="bg-white rounded-[24px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.03)] border border-indigo-100 relative overflow-hidden active:scale-[0.98] transition-transform cursor-pointer">
                <div className="absolute top-0 right-0 bg-indigo-500 text-white px-3 py-1.5 rounded-bl-[16px] font-bold text-[11px] shadow-sm">匹配度 99%</div>
                <div className="flex flex-wrap gap-2 mb-4 mt-1">
                  <span className="bg-[#F7F9FC] text-gray-600 px-2 py-0.5 rounded-[6px] text-[11px] font-bold border border-gray-100">演示题库</span>
                  <span className="bg-[#F7F9FC] text-gray-600 px-2 py-0.5 rounded-[6px] text-[11px] font-bold border border-gray-100">资料分析</span>
                </div>
                <p className="text-[15px] font-bold text-gray-900 leading-[1.6] line-clamp-3">
                  {mockQ.text}
                </p>
             </div>

             {/* Hit 2 */}
             <div onClick={() => setView("solution_bank")} className="bg-white rounded-[24px] p-6 shadow-[0_2px_10px_rgba(0,0,0,0.01)] border border-gray-100 active:scale-[0.98] transition-transform cursor-pointer opacity-70 hover:opacity-100">
                <div className="flex flex-wrap gap-2 mb-4">
                  <span className="bg-[#F7F9FC] text-gray-600 px-2 py-0.5 rounded-[6px] text-[11px] font-bold border border-gray-100">2021年联考</span>
                  <span className="bg-[#F7F9FC] text-gray-600 px-2 py-0.5 rounded-[6px] text-[11px] font-bold border border-gray-100">资料分析</span>
                </div>
                <p className="text-[15px] font-bold text-gray-900 leading-[1.6] line-clamp-3">
                  2021年，某省城镇居民人均可支配收入为48000元，农村为20000元...
                </p>
             </div>

             {/* Fallback */}
             <div className="mt-6 bg-[#F7F9FC] border-2 border-gray-200 border-dashed rounded-[24px] p-6 flex flex-col items-center text-center">
                <h3 className="text-[16px] font-bold text-gray-900 mb-2">都不是我要找的题？</h3>
                <p className="text-[13px] text-gray-500 font-medium mb-5 leading-relaxed px-4">如果题库未能精准命中，你可以让岸岸作为新题直接进行计算推理。</p>
                <button onClick={() => setView("no_hit")} className="px-6 py-3 bg-white border border-gray-200 rounded-full text-[14px] font-bold text-gray-900 shadow-sm active:scale-95 transition-transform flex items-center gap-2">
                  <Search size={16} className="text-gray-400" /> 未在题库中找到完全一致的题目
                </button>
             </div>
          </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 6. NO HIT
  // -------------------------------------------------------------------------
  if (view === "no_hit") {
    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans items-center justify-center text-center px-8">
        <div className="h-[48px] w-full shrink-0 z-50 absolute top-0 pointer-events-none" />
        <button onClick={() => setView("multi_hit")} className="absolute top-[64px] left-4 p-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>

        <div className="w-20 h-20 bg-white rounded-full flex items-center justify-center mb-6 shadow-sm border border-gray-100">
          <Search size={32} className="text-gray-300" />
        </div>
        <h2 className="text-[20px] font-bold text-gray-900 mb-3 tracking-tight">未在题库中找到完全一致的题目</h2>
        <p className="text-[14px] text-gray-500 font-medium mb-10 leading-relaxed max-w-[280px]">
          这可能是一道新题或小众考卷题。岸岸可以为你独立进行 AI 深度推理与解答。
        </p>
        <button onClick={() => { setView("searching_ai"); setTimeout(() => setView("solution_ai"), 1500); }} className="w-full py-4 bg-cyan-500 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_20px_rgba(6,182,212,0.3)] active:scale-95 transition-transform flex items-center justify-center gap-2">
          <Brain size={18} /> 使用 AI 独立解题
        </button>
        <button onClick={() => setView("camera")} className="mt-6 text-[14px] font-bold text-gray-500 active:scale-95 flex items-center gap-1"><Camera size={14}/> 重新拍摄</button>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // 7. SOLUTION VIEWS (题库解析 vs AI 独立解析)
  // -------------------------------------------------------------------------
  if (view === "solution_bank" || view === "solution_ai") {
    const isAI = view === "solution_ai";

    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans">
        <div className="h-[48px] w-full shrink-0 z-50 pointer-events-none" />
        
        <div className="h-14 flex items-center justify-between px-4 shrink-0 z-10 mt-[48px] bg-[#F7F9FC]">
          <button onClick={() => setView("multi_hit")} className="p-2 -ml-2 active:scale-95 text-gray-900"><ChevronLeft size={24} /></button>
          <span className="text-[16px] font-bold text-gray-900">题目解析</span>
          <button onClick={() => setView("camera")} className="p-2 -mr-2 text-gray-500 hover:text-gray-900"><Camera size={20}/></button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 pt-2 pb-[140px] no-scrollbar">
           {/* Banner for AI Independent */}
           {isAI && (
             <div className="bg-gradient-to-r from-cyan-500 to-blue-500 rounded-[16px] p-4 mb-6 flex items-start gap-3 shadow-[0_4px_15px_rgba(6,182,212,0.2)]">
                <Brain size={20} className="text-white shrink-0 mt-0.5" />
                <div>
                  <h4 className="text-[14px] font-bold text-white mb-1">AI 根据图片独立推理</h4>
                  <p className="text-[12px] text-cyan-50 font-medium leading-relaxed">
                    此解析非题库标准答案，由大模型实时生成。请结合推理过程自行判断。
                  </p>
                </div>
             </div>
           )}

           {/* Question Info */}
           <div className="mb-6">
              <div className="flex flex-wrap gap-2 mb-3">
                <span className="bg-white border border-gray-100 text-gray-500 px-2.5 py-1 rounded-[8px] text-[11px] font-bold shadow-sm">
                  {isAI ? "来源：用户上传图片" : `来源：${mockQ.source}`}
                </span>
                <span className="bg-white border border-gray-100 text-gray-500 px-2.5 py-1 rounded-[8px] text-[11px] font-bold shadow-sm">资料分析</span>
              </div>
              <p className="text-[16px] font-bold text-gray-900 leading-[1.6]">
                {mockQ.text}
              </p>
           </div>

           {/* Answer Area */}
           <div className="bg-white rounded-[24px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 mb-8 relative mt-6">
              <div className={`absolute -top-4 left-6 px-4 py-1.5 rounded-full text-[13px] font-bold border-2 border-white shadow-sm flex items-center gap-1.5 ${isAI ? "bg-cyan-500 text-white" : "bg-green-500 text-white"}`}>
                 {isAI ? <Brain size={14}/> : <CheckCircle2 size={14}/>}
                 {isAI ? "AI 推理答案：C" : "正确答案：C"}
              </div>
              <div className="pt-4 flex flex-col gap-3">
                 {mockQ.options.map((opt, i) => {
                   const isCorrect = opt.startsWith("C");
                   return (
                     <p key={i} className={`text-[15px] font-bold ${isCorrect ? (isAI ? "text-cyan-600" : "text-green-600") : "text-gray-400 line-through decoration-gray-300"}`}>
                       {opt}
                     </p>
                   );
                 })}
              </div>
           </div>

           {/* Explanation */}
           <div className="mb-8">
              <div className="flex items-center gap-2 mb-5">
                 <div className={`w-8 h-8 rounded-full flex items-center justify-center ${isAI ? "bg-cyan-50" : "bg-indigo-50"}`}>
                   <Sparkles size={16} className={isAI ? "text-cyan-500" : "text-indigo-500"} />
                 </div>
                 <h3 className="text-[18px] font-bold text-gray-900">{isAI ? "AI 推理过程" : "岸岸精讲"}</h3>
                 {!isAI && <span className="text-[11px] font-bold text-indigo-500 bg-indigo-50 px-2 py-0.5 rounded-md ml-auto">题库已验证解析</span>}
              </div>

              <div className="flex flex-col gap-6">
                 {/* Step 1 */}
                 <div className="bg-white rounded-[20px] p-5 shadow-[0_2px_15px_rgba(0,0,0,0.01)] border border-gray-50">
                   <h4 className="text-[14px] font-bold text-gray-900 mb-2 flex items-center gap-2">
                     <div className={`w-2 h-2 rounded-full ${isAI ? "bg-cyan-400" : "bg-indigo-400"}`} /> 提取材料数据
                   </h4>
                   <p className="text-[14px] text-gray-700 leading-relaxed font-medium">
                     城镇居民人均可支配收入 = 49283元，农村居民人均可支配收入 = 20133元。城镇与农村人口比例为 2:1。
                   </p>
                 </div>
                 {/* Step 2 */}
                 <div className="bg-white rounded-[20px] p-5 shadow-[0_2px_15px_rgba(0,0,0,0.01)] border border-gray-50">
                   <h4 className="text-[14px] font-bold text-gray-900 mb-3 flex items-center gap-2">
                     <div className={`w-2 h-2 rounded-full ${isAI ? "bg-cyan-400" : "bg-indigo-400"}`} /> 计算全口径平均数
                   </h4>
                   <p className="text-[14px] text-gray-700 leading-relaxed font-medium mb-3">
                     根据加权平均公式：全市人均收入 = (城镇收入 × 城镇比例 + 农村收入 × 农村比例) / 总比例。
                   </p>
                   <div className="bg-[#F7F9FC] p-4 rounded-[16px] font-mono text-[14px] font-bold text-gray-800 border border-gray-100 flex items-center justify-center">
                      {mockQ.calc}
                   </div>
                 </div>
              </div>
           </div>

           {/* AI Actions */}
           <div className={`rounded-[24px] p-5 border flex flex-col gap-3 ${isAI ? "bg-cyan-50/30 border-cyan-100" : "bg-indigo-50/30 border-indigo-100"}`}>
              <p className="text-[14px] font-bold text-gray-900 mb-1">对这道题还有疑问？</p>
              <div className="flex gap-2">
                <button className="flex-1 py-2.5 bg-white rounded-[12px] border border-gray-100 text-[13px] font-bold text-gray-600 shadow-sm active:scale-95 transition-transform">
                  十字交叉法怎么用
                </button>
                <button className="flex-1 py-2.5 bg-white rounded-[12px] border border-gray-100 text-[13px] font-bold text-gray-600 shadow-sm active:scale-95 transition-transform">
                  帮我找相似题
                </button>
              </div>
              <button onClick={() => onGo("coach")} className={`w-full mt-1 py-3.5 bg-white rounded-[16px] border text-[14px] font-bold flex items-center justify-center gap-1.5 shadow-sm active:scale-[0.98] transition-transform ${isAI ? "text-cyan-600 border-cyan-100" : "text-indigo-600 border-indigo-100"}`}>
                <MessageSquare size={16} /> 继续追问岸岸
              </button>
           </div>
        </div>

        {/* Bottom Actions -> Opens Drawer */}
        <div className="absolute bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-gray-100 p-5 pt-4 pb-safe z-40">
           <button onClick={() => setRecordDrawer(true)} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
             这道题该如何处理？
           </button>
        </div>

        {/* Record Drawer */}
        <AnimatePresence>
          {recordDrawer && (
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 bg-gray-900/40 backdrop-blur-sm flex flex-col justify-end"
              onClick={() => setRecordDrawer(false)}
            >
              <motion.div 
                initial={{ y: "100%" }}
                animate={{ y: 0 }}
                exit={{ y: "100%" }}
                transition={{ type: "spring", damping: 25, stiffness: 200 }}
                className="bg-[#F7F9FC] rounded-t-[32px] pt-4 pb-safe flex flex-col"
                onClick={e => e.stopPropagation()}
              >
                <div className="w-12 h-1.5 bg-gray-200 rounded-full mx-auto mb-5 shrink-0" />
                <div className="flex justify-between items-center px-6 mb-4">
                  <h3 className="text-[20px] font-bold text-gray-900 tracking-tight">收录与个人画像边界</h3>
                  <button onClick={() => setRecordDrawer(false)} className="w-8 h-8 rounded-full bg-gray-50 flex items-center justify-center active:scale-95">
                    <X size={16} className="text-gray-500" />
                  </button>
                </div>
                
                <div className="px-6 pb-2 text-[13px] font-medium text-gray-500 leading-relaxed mb-4">
                  拍照搜题本身不能自动证明你会做或不会做，请明确告诉岸岸这道题如何计入你的学习记录。
                </div>

                <div className="px-6 flex flex-col gap-3 pb-8">
                   {/* Option 1 */}
                   <button 
                     onClick={() => setRecordType("view")}
                     className={`p-5 rounded-[24px] text-left border-[1.5px] transition-all ${recordType === "view" ? "border-gray-900 bg-white shadow-[0_4px_20px_rgba(0,0,0,0.04)]" : "border-transparent bg-white shadow-sm"}`}
                   >
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-[16px] font-bold text-gray-900">仅本次查看</span>
                        {recordType === "view" && <CheckCircle2 size={20} className="text-gray-900" />}
                      </div>
                      <p className="text-[13px] text-gray-500 font-medium">不进入用户的学习记录，不改变能力分。</p>
                   </button>

                   {/* Option 2 */}
                   <button 
                     onClick={() => setRecordType("mistake")}
                     className={`p-5 rounded-[24px] text-left border-[1.5px] transition-all ${recordType === "mistake" ? "border-gray-900 bg-white shadow-[0_4px_20px_rgba(0,0,0,0.04)]" : "border-transparent bg-white shadow-sm"}`}
                   >
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-[16px] font-bold text-gray-900">加入错题本</span>
                        {recordType === "mistake" && <CheckCircle2 size={20} className="text-gray-900" />}
                      </div>
                      <p className="text-[13px] text-gray-500 font-medium">只安排后续复练，不直接改变当前能力分。</p>
                   </button>

                   {/* Option 3 */}
                   <button 
                     onClick={() => setRecordType("real")}
                     className={`p-5 rounded-[24px] text-left border-[1.5px] transition-all flex flex-col ${recordType === "real" ? "border-indigo-500 bg-indigo-50/30 shadow-[0_4px_20px_rgba(0,0,0,0.04)]" : "border-transparent bg-white shadow-sm"}`}
                   >
                      <div className="flex justify-between items-center w-full mb-1">
                        <span className="text-[16px] font-bold text-indigo-900">记录为我的真实作答</span>
                        {recordType === "real" && <CheckCircle2 size={20} className="text-indigo-600" />}
                      </div>
                      <p className="text-[13px] text-indigo-700/70 font-medium mb-4">补充你的实际作答情况，作为能力画像证据。</p>
                      
                      {/* Expanded inputs */}
                      <AnimatePresence>
                        {recordType === "real" && (
                          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} className="overflow-hidden w-full pt-4 border-t border-indigo-100/50">
                             <p className="text-[13px] font-bold text-indigo-900 mb-3">你的答案是？ <span className="text-red-500 text-[11px] ml-1">(必选)</span></p>
                             <div className="flex gap-2 mb-5">
                               {["A", "B", "C", "D"].map(opt => (
                                 <div 
                                   key={opt} 
                                   onClick={(e) => { e.stopPropagation(); setUserAnswer(opt); }}
                                   className={`flex-1 aspect-square rounded-[16px] flex items-center justify-center font-bold text-[16px] border-2 transition-all ${userAnswer === opt ? "bg-indigo-500 text-white border-indigo-500 shadow-md" : "bg-white text-gray-600 border-gray-100"}`}
                                 >
                                   {opt}
                                 </div>
                               ))}
                             </div>
                             
                             <div className="flex gap-3">
                               <div className="flex-1 bg-white border border-gray-100 rounded-[16px] p-3 flex flex-col shadow-sm">
                                 <span className="text-[11px] text-gray-400 font-bold mb-1">用时 (可选)</span>
                                 <input 
                                   type="text" 
                                   value={timeSpent}
                                   onChange={(e) => { e.stopPropagation(); setTimeSpent(e.target.value); }}
                                   onClick={(e) => e.stopPropagation()}
                                   placeholder="如: 65秒" 
                                   className="bg-transparent border-none text-[14px] font-bold text-gray-900 focus:outline-none placeholder:text-gray-300 w-full" 
                                 />
                               </div>
                               <div className="flex-1 bg-white border border-gray-100 rounded-[16px] p-3 flex flex-col shadow-sm">
                                 <span className="text-[11px] text-gray-400 font-bold mb-1">信心 (可选)</span>
                                 <select 
                                   value={confidence}
                                   onChange={(e) => { e.stopPropagation(); setConfidence(e.target.value); }}
                                   onClick={(e) => e.stopPropagation()}
                                   className="bg-transparent border-none text-[14px] font-bold text-gray-900 focus:outline-none appearance-none w-full"
                                 >
                                    <option>无</option>
                                    <option>猜的</option>
                                    <option>模糊</option>
                                    <option>正常</option>
                                    <option>确定</option>
                                 </select>
                               </div>
                             </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                   </button>
                </div>

                <div className="px-6 pb-safe bg-white pt-5 border-t border-gray-100">
                  <button 
                    disabled={!recordType || (recordType === "real" && !userAnswer)}
                    onClick={() => { setRecordDrawer(false); setView("record_success"); }}
                    className={`w-full py-4 rounded-[24px] font-bold text-[16px] transition-all ${(!recordType || (recordType === "real" && !userAnswer)) ? "bg-gray-100 text-gray-400" : "bg-gray-900 text-white shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98]"}`}
                  >
                    确认处理
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
  // 8. RECORD SUCCESS (处理回执)
  // -------------------------------------------------------------------------
  if (view === "record_success") {
    const isReal = recordType === "real";
    const isMistake = recordType === "mistake";
    const isCorrect = isReal && userAnswer === mockQ.correct;

    return (
      <div className="flex flex-col h-full bg-[#F7F9FC] relative font-sans items-center justify-center px-6">
        <div className="h-[48px] w-full shrink-0 z-50 absolute top-0 pointer-events-none" />
        
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.4, ease: "easeOut" }}
          className="flex flex-col items-center w-full"
        >
          <div className="w-20 h-20 bg-white rounded-[28px] flex items-center justify-center shadow-[0_8px_30px_rgba(0,0,0,0.04)] mb-6 border border-gray-50">
            <CheckCircle2 size={36} className="text-green-500" />
          </div>
          <h2 className="text-[26px] font-bold text-gray-900 tracking-tight mb-8">处理成功</h2>
          
          <div className="w-full bg-white rounded-[32px] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.02)] border border-gray-50 flex flex-col gap-5 text-left mb-10">
             <div className="flex items-center justify-between">
                <span className="text-[14px] font-bold text-gray-500">记录位置</span>
                <span className="text-[14px] font-bold text-gray-900">
                  {isReal ? "我的真实作答" : (isMistake ? "错题本" : "仅查看 (未保存)")}
                </span>
             </div>
             
             {isReal && (
               <>
                 <div className="h-px bg-gray-50" />
                 <div className="flex items-center justify-between">
                    <span className="text-[14px] font-bold text-gray-500">作答结果</span>
                    <span className={`text-[14px] font-bold flex items-center gap-1 ${isCorrect ? "text-green-600" : "text-red-500"}`}>
                      {isCorrect ? "正确" : "回答错误"} (你选了 {userAnswer})
                    </span>
                 </div>
                 <div className="h-px bg-gray-50" />
                 <div className="flex items-center justify-between">
                    <span className="text-[14px] font-bold text-gray-500">画像影响</span>
                    <span className="text-[14px] font-bold text-indigo-600 flex items-center gap-1"><Sparkles size={12}/> 已计入能力证据</span>
                 </div>
               </>
             )}

             {isMistake && (
               <>
                 <div className="h-px bg-gray-50" />
                 <div className="flex items-center justify-between">
                    <span className="text-[14px] font-bold text-gray-500">画像影响</span>
                    <span className="text-[14px] font-bold text-gray-400">不直接改变能力分</span>
                 </div>
               </>
             )}

             {(isMistake || (!isCorrect && isReal)) && (
               <>
                 <div className="h-px bg-gray-50" />
                 <div className="flex items-center justify-between">
                    <span className="text-[14px] font-bold text-gray-500">后续安排</span>
                    <span className="text-[14px] font-bold text-gray-900">等待智能复练</span>
                 </div>
               </>
             )}
          </div>

          <div className="w-full flex flex-col gap-3">
             <button onClick={() => onGo("today")} className="w-full py-4 bg-gray-900 text-white rounded-[24px] font-bold text-[16px] shadow-[0_8px_30px_rgba(0,0,0,0.12)] active:scale-[0.98] transition-transform">
               回到首页
             </button>
             <div className="flex gap-3">
               <button onClick={() => setView("solution_bank")} className="flex-1 py-3.5 bg-transparent border border-gray-200 text-gray-600 rounded-[20px] font-bold text-[14px] active:scale-[0.98] transition-transform flex justify-center items-center gap-1.5">
                 <Undo2 size={16} /> 撤销收录
               </button>
               <button onClick={() => { setView("camera"); setRecordType(null); setUserAnswer(null); }} className="flex-1 py-3.5 bg-white shadow-sm border border-gray-100 text-gray-900 rounded-[20px] font-bold text-[14px] active:scale-[0.98] transition-transform">
                 继续搜题
               </button>
             </div>
          </div>
        </motion.div>
      </div>
    );
  }

  return null;
};
