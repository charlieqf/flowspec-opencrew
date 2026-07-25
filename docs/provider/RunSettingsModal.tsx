import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { X, Sparkles, Circle, CheckCircle2 } from '../icons';
import { cn } from '../utils';

// Extra Zap icon for "Flash" speed
const Zap = ({ size = 24, className, ...props }: any) => (
  <svg
    xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    className={className} {...props}
  >
    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
  </svg>
);

export function RunSettingsModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const [selectedModel, setSelectedModel] = useState<'max' | 'flash'>('max');

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6 bg-slate-900/40 backdrop-blur-sm">
        <motion.div
          initial={{ opacity: 0, scale: 0.96, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.96, y: 10 }}
          className="w-full max-w-[500px] bg-white dark:bg-slate-950 rounded-2xl shadow-[0_20px_50px_-12px_rgba(0,0,0,0.25)] border border-slate-200 dark:border-slate-800 flex flex-col overflow-hidden"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 pt-6 pb-5">
            <div className="flex items-center gap-3">
              <h2 className="text-xl font-extrabold text-slate-800 dark:text-slate-100 tracking-tight">运行设置</h2>
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-400 border border-indigo-200/50 dark:border-indigo-500/30">
                  Task #66
                </span>
                <span className="text-slate-300 dark:text-slate-600 text-xs font-bold">/</span>
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-100 text-rose-700 dark:bg-rose-500/20 dark:text-rose-400 border border-rose-200/50 dark:border-rose-500/30">
                  Session #125
                </span>
              </div>
            </div>
          </div>

          {/* Main Content Area */}
          <div className="px-6 py-2">
            <div className="grid grid-cols-2 gap-3 mb-6">
              {/* Max Engine Card */}
              <button
                onClick={() => setSelectedModel('max')}
                className={cn(
                  "relative group text-left p-4 rounded-xl border-2 transition-all duration-200 overflow-hidden",
                  selectedModel === 'max'
                    ? "border-indigo-600 bg-indigo-50/50 dark:bg-indigo-500/10 dark:border-indigo-500 ring-4 ring-indigo-600/10 dark:ring-indigo-500/20 shadow-md"
                    : "border-slate-200 hover:border-slate-300 bg-white dark:bg-slate-900 dark:border-slate-800 dark:hover:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800/80 shadow-sm"
                )}
              >
                {selectedModel === 'max' && (
                  <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-indigo-500 to-purple-500" />
                )}
                <div className="flex items-center justify-between mb-3">
                  <div className={cn(
                    "p-2 rounded-lg",
                    selectedModel === 'max' 
                      ? "bg-indigo-100 text-indigo-600 dark:bg-indigo-500/20 dark:text-indigo-400" 
                      : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
                  )}>
                    <Sparkles size={18} />
                  </div>
                  {selectedModel === 'max' ? (
                     <CheckCircle2 size={18} className="text-indigo-600 dark:text-indigo-500" />
                  ) : (
                     <Circle size={18} className="text-slate-300 dark:text-slate-700" />
                  )}
                </div>
                <h3 className={cn(
                  "font-bold text-base mb-1 transition-colors",
                  selectedModel === 'max' ? "text-indigo-900 dark:text-indigo-100" : "text-slate-700 dark:text-slate-300"
                )}>Max</h3>
                <p className={cn(
                  "text-[11px] font-medium leading-relaxed transition-colors",
                   selectedModel === 'max' ? "text-indigo-700/80 dark:text-indigo-300/80" : "text-slate-500"
                )}>
                  Full reasoning capacity. Highest quality output and complex planning.
                </p>
              </button>

              {/* Flash Engine Card */}
              <button
                onClick={() => setSelectedModel('flash')}
                className={cn(
                  "relative group text-left p-4 rounded-xl border-2 transition-all duration-200 overflow-hidden",
                  selectedModel === 'flash'
                    ? "border-sky-500 bg-sky-50/50 dark:bg-sky-500/10 dark:border-sky-500 ring-4 ring-sky-500/10 dark:ring-sky-500/20 shadow-md"
                    : "border-slate-200 hover:border-slate-300 bg-white dark:bg-slate-900 dark:border-slate-800 dark:hover:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800/80 shadow-sm"
                )}
              >
                {selectedModel === 'flash' && (
                  <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-sky-400 to-cyan-400" />
                )}
                <div className="flex items-center justify-between mb-3">
                  <div className={cn(
                    "p-2 rounded-lg",
                    selectedModel === 'flash' 
                      ? "bg-sky-100 text-sky-600 dark:bg-sky-500/20 dark:text-sky-400" 
                      : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
                  )}>
                    <Zap size={18} />
                  </div>
                  {selectedModel === 'flash' ? (
                     <CheckCircle2 size={18} className="text-sky-600 dark:text-sky-500" />
                  ) : (
                     <Circle size={18} className="text-slate-300 dark:text-slate-700" />
                  )}
                </div>
                <h3 className={cn(
                  "font-bold text-base mb-1 transition-colors",
                  selectedModel === 'flash' ? "text-sky-900 dark:text-sky-100" : "text-slate-700 dark:text-slate-300"
                )}>Flash</h3>
                <p className={cn(
                  "text-[11px] font-medium leading-relaxed transition-colors",
                   selectedModel === 'flash' ? "text-sky-700/80 dark:text-sky-300/80" : "text-slate-500"
                )}>
                  Optimized for speed. Fastest execution for straightforward tasks.
                </p>
              </button>
            </div>
          </div>
          
          {/* Footer */}
          <div className="px-6 py-5 bg-slate-50 dark:bg-slate-900/50 border-t border-slate-100 dark:border-slate-800/60 flex items-center justify-end gap-3 mt-2">
            <button 
              onClick={onClose}
              className="px-5 py-2.5 text-[13px] font-bold text-slate-600 dark:text-slate-300 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800 hover:text-slate-900 dark:hover:text-slate-100 rounded-lg transition-colors shadow-sm"
            >
              取消
            </button>
            <button 
              onClick={onClose}
              className="px-6 py-2.5 text-[13px] font-bold text-white bg-indigo-600 hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600 rounded-lg transition-all shadow-md shadow-indigo-500/20 outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 dark:focus:ring-offset-slate-900"
            >
              进入任务
            </button>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
