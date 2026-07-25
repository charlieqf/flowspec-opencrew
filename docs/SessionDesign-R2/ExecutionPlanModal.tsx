import React, { useState } from 'react';
import { motion } from 'motion/react';
type IconProps = React.SVGProps<SVGSVGElement> & { size?: number | string };

const createIcon = (content: React.ReactNode) => {
    return function Icon({ size = 24, className, ...props }: IconProps) {
        return (
            <svg
                xmlns="http://www.w3.org/2000/svg"
                width={size}
                height={size}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className={className}
                {...props}
            >
                {content}
            </svg>
        );
    };
};

const X = createIcon(<path d="M18 6 6 18M6 6l12 12" />);
const CheckCircle2 = createIcon(<><circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" /></>);
const Circle = createIcon(<circle cx="12" cy="12" r="10" />);
const ListTodo = createIcon(<><rect x="3" y="5" width="6" height="6" rx="1" /><path d="m3 17 2 2 4-4" /><path d="M13 6h8" /><path d="M13 12h8" /><path d="M13 18h8" /></>);
const Mic = createIcon(<><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" /><path d="M19 10v2a7 7 0 0 1-14 0v-2" /><line x1="12" x2="12" y1="19" y2="22" /></>);
const ImageIcon = createIcon(<><rect width="18" height="18" x="3" y="3" rx="2" ry="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" /></>);
const Clapperboard = createIcon(<><path d="M20.2 6 3 11l-.9-2.4c-.3-1.1.3-2.2 1.3-2.5l13.5-4c1.1-.3 2.2.3 2.5 1.3Z" /><path d="m6.2 5.3 3.1 3.9" /><path d="m12.4 3.4 3.1 4" /><path d="M3 11h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></>);
const AudioLines = createIcon(<><path d="M2 10v3" /><path d="M6 6v11" /><path d="M10 3v18" /><path d="M14 8v7" /><path d="M18 5v13" /><path d="M22 10v3" /></>);
const Clock = createIcon(<><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></>);
const LayoutList = createIcon(<><rect width="7" height="7" x="3" y="3" rx="1" /><rect width="7" height="7" x="3" y="14" rx="1" /><path d="M14 4h7" /><path d="M14 9h7" /><path d="M14 15h7" /><path d="M14 20h7" /></>);
const Check = createIcon(<path d="M20 6 9 17l-5-5" />);
const Workflow = createIcon(<><rect width="8" height="8" x="3" y="3" rx="2" /><path d="M7 11v4a2 2 0 0 0 2 2h4" /><rect width="8" height="8" x="13" y="13" rx="2" /></>);
const Film = createIcon(<><rect width="18" height="18" x="3" y="3" rx="2" /><path d="M7 3v18" /><path d="M3 7.5h4" /><path d="M3 12h18" /><path d="M3 16.5h4" /><path d="M17 3v18" /><path d="M17 7.5h4" /><path d="M17 16.5h4" /></>);
const Layers = createIcon(<><path d="M12 2l-10 5 10 5 10-5-10-5z" /><path d="M2 12l10 5 10-5" /><path d="M2 17l10 5 10-5" /></>);
const Play = createIcon(<polygon points="5 3 19 12 5 21 5 3" />);
const Sun = createIcon(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" /></>);
const Moon = createIcon(<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />);
import { cn } from '../utils';
import { useEditor } from '../store';

// Mock Data from the provided JSON
const planData = {
    "summary": {
        "shot_count": 4,
        "scene_count": 12,
        "dialogue_count": 42,
        "segment_count": 19,
        "need_audio_count": 42,
        "need_image_count": 12,
        "need_video_count": 19,
        "need_lipsync_count": 19
    },
    "shots": [
        {
            "shot_id": "shot_001",
            "status": "planned",
            "scenes": [
                {
                    "scene_id": "scene_001",
                    "start": 0.205,
                    "end": 6.68,
                    "duration": 6.475,
                    "segments": [
                        {
                            "segment_id": "shot_001_scene_001_segment_001",
                            "segment_index": 1,
                            "status": "planned",
                            "start": 0.205,
                            "end": 4.582,
                            "duration": 4.377,
                            "tasks": {
                                "need_audio": true,
                                "need_image": true,
                                "need_video": true,
                                "need_lipsync": true
                            }
                        },
                        {
                            "segment_id": "shot_001_scene_001_segment_002",
                            "segment_index": 2,
                            "status": "planned",
                            "start": 4.582,
                            "end": 6.68,
                            "duration": 2.098,
                            "tasks": {
                                "need_audio": true,
                                "need_image": false,
                                "need_video": true,
                                "need_lipsync": true
                            }
                        }
                    ]
                },
                {
                    "scene_id": "scene_002",
                    "start": 6.68,
                    "end": 10.68,
                    "duration": 4.0,
                    "segments": [
                        {
                            "segment_id": "shot_001_scene_002_segment_001",
                            "segment_index": 1,
                            "status": "planned",
                            "start": 6.68,
                            "end": 10.68,
                            "duration": 4.0,
                            "tasks": {
                                "need_audio": true,
                                "need_image": true,
                                "need_video": true,
                                "need_lipsync": true
                            }
                        }
                    ]
                }
            ]
        },
        {
            "shot_id": "shot_002",
            "status": "planned",
            "scenes": [
                {
                    "scene_id": "scene_004",
                    "start": 16.04,
                    "end": 23.48,
                    "duration": 7.44,
                    "segments": [
                        {
                            "segment_id": "shot_002_scene_004_segment_001",
                            "segment_index": 1,
                            "status": "planned",
                            "start": 16.04,
                            "end": 20.08,
                            "duration": 4.04,
                            "tasks": {
                                "need_audio": true,
                                "need_image": true,
                                "need_video": true,
                                "need_lipsync": true
                            }
                        },
                        {
                            "segment_id": "shot_002_scene_004_segment_002",
                            "segment_index": 2,
                            "status": "planned",
                            "start": 20.56,
                            "end": 23.48,
                            "duration": 2.92,
                            "tasks": {
                                "need_audio": true,
                                "need_image": false,
                                "need_video": true,
                                "need_lipsync": true
                            }
                        }
                    ]
                }
            ]
        }
    ]
};

function formatTime(seconds: number) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    const ms = Math.floor((seconds % 1) * 100);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`;
}

function TaskBadge({ active, icon: Icon, label }: { active: boolean, icon: any, label: string }) {
    if (!active) {
        return (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-slate-100 dark:bg-slate-800/50 text-slate-400 dark:text-slate-500 border border-transparent blur-[0.5px] opacity-60">
                <Icon size={12} />
                <span className="text-[10px] font-semibold uppercase">{label}</span>
            </div>
        );
    }

    return (
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 shadow-sm relative group">
            <div className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-amber-400 border-2 border-white dark:border-slate-900 z-10 hidden group-hover:block" />
            <Icon size={12} className="text-indigo-500" />
            <span className="text-[10px] font-bold uppercase tracking-wider">{label}</span>
        </div>
    );
}

export function ExecutionPlanModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
    const { theme, setTheme } = useEditor();
    const [progress, setProgress] = useState({ audio: 0, image: 0, video: 0, lipsync: 0 });
    const [isExecuting, setIsExecuting] = useState(false);

    const handleExecute = () => {
        if (isExecuting) return;
        setIsExecuting(true);

        // Fake progress
        let it = 0;
        const interval = setInterval(() => {
            it++;
            setProgress(p => ({
                audio: Math.min(planData.summary.need_audio_count, p.audio + 2),
                image: Math.min(planData.summary.need_image_count, p.image + 1),
                video: Math.min(planData.summary.need_video_count, p.video + (it % 2 === 0 ? 1 : 0)),
                lipsync: Math.min(planData.summary.need_lipsync_count, p.lipsync + (it % 3 === 0 ? 1 : 0))
            }));

            if (it > 25) {
                clearInterval(interval);
                setIsExecuting(false);
            }
        }, 400);
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6 bg-slate-900/60 backdrop-blur-md">
            <motion.div
                initial={{ opacity: 0, scale: 0.96, y: 10 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96, y: 10 }}
                className="w-full max-w-6xl bg-slate-50 dark:bg-slate-950 rounded-2xl shadow-2xl overflow-hidden border border-slate-200 dark:border-slate-800 flex flex-col h-[85vh]"
            >
                {/* Header */}
                <div className="flex items-center justify-between p-[18px] border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 z-10 shrink-0">
                    <div className="flex items-center gap-6">
                        <div className="flex items-center gap-3 w-[260px] shrink-0">
                            <div className="p-2 bg-indigo-100 dark:bg-indigo-500/20 text-indigo-600 dark:text-indigo-400 rounded-lg">
                                <Workflow size={18} />
                            </div>
                            <h2 className="text-[17px] font-bold text-slate-800 dark:text-slate-100 tracking-tight">Generation Plan</h2>
                        </div>

                        {/* Two-row Task Progress Panel */}
                        <div className="flex flex-col gap-1.5 border-l border-slate-200 dark:border-slate-800 pl-6">
                            <div className="flex items-center gap-4 text-[11px] font-medium text-slate-500 dark:text-slate-400">
                                <span className="flex items-center gap-1.5"><Film size={12} /> {planData.summary.shot_count} Shots</span>
                                <span className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-700" />
                                <span className="flex items-center gap-1.5"><Layers size={12} /> {planData.summary.scene_count} Scenes</span>
                                <span className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-700" />
                                <span className="flex items-center gap-1.5"><LayoutList size={12} /> {planData.summary.segment_count} Segments</span>
                            </div>

                            <div className="flex items-center gap-4">
                                <div className="flex items-center gap-1.5 text-xs font-mono font-bold text-slate-700 dark:text-slate-300" title="Audio Tasks">
                                    <Mic size={14} className="text-indigo-500" />
                                    {progress.audio} <span className="text-slate-400 text-[10px]">/ {planData.summary.need_audio_count}</span>
                                </div>
                                <div className="flex items-center gap-1.5 text-xs font-mono font-bold text-slate-700 dark:text-slate-300" title="Image Tasks">
                                    <ImageIcon size={14} className="text-emerald-500" />
                                    {progress.image} <span className="text-slate-400 text-[10px]">/ {planData.summary.need_image_count}</span>
                                </div>
                                <div className="flex items-center gap-1.5 text-xs font-mono font-bold text-slate-700 dark:text-slate-300" title="Video Tasks">
                                    <Clapperboard size={14} className="text-blue-500" />
                                    {progress.video} <span className="text-slate-400 text-[10px]">/ {planData.summary.need_video_count}</span>
                                </div>
                                <div className="flex items-center gap-1.5 text-xs font-mono font-bold text-slate-700 dark:text-slate-300" title="Lip Sync Tasks">
                                    <AudioLines size={14} className="text-amber-500" />
                                    {progress.lipsync} <span className="text-slate-400 text-[10px]">/ {planData.summary.need_lipsync_count}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-1">
                        <button
                            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
                            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-md transition-colors"
                            title="Toggle theme"
                        >
                            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
                        </button>
                        <button
                            onClick={onClose}
                            className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-md transition-colors"
                        >
                            <X size={18} />
                        </button>
                    </div>
                </div>

                <div className="flex-1 overflow-hidden flex bg-slate-50/50 dark:bg-slate-950/50">
                    {/* Right Main Timeline Panel */}
                    <div className="flex-1 overflow-y-auto p-[18px] relative">
                        <div className="max-w-4xl mx-auto space-y-6">
                            {planData.shots.map((shot, index) => (
                                <div key={shot.shot_id} className="relative">
                                    <div className="sticky top-0 z-10 bg-slate-50/90 dark:bg-slate-950/90 backdrop-blur-sm py-2 mb-4 flex items-center justify-between border-b border-slate-200 dark:border-slate-800">
                                        <h3 className="text-lg font-bold text-slate-800 dark:text-slate-200 tracking-tight uppercase">
                                            {shot.shot_id.replace('_', ' ')}
                                        </h3>
                                        {index === 0 && (
                                            <button
                                                onClick={handleExecute}
                                                disabled={isExecuting || progress.audio === planData.summary.need_audio_count}
                                                className="p-3 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-400 dark:disabled:bg-slate-700 text-white rounded-xl shadow-md shadow-indigo-500/20 disabled:shadow-none transition-all flex items-center justify-center shrink-0"
                                                title="Execute Plan"
                                            >
                                                {isExecuting ? <Clock size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
                                            </button>
                                        )}
                                    </div>

                                    <div className="space-y-6">
                                        {shot.scenes.map(scene => (
                                            <div key={scene.scene_id} className="ml-2 pl-6 md:ml-4 md:pl-6 border-l border-slate-200 dark:border-slate-800 relative">
                                                {/* Scene node marker */}
                                                <div className="absolute -left-[5px] top-1.5 w-2.5 h-2.5 rounded-full bg-slate-200 dark:bg-slate-700 border-2 border-slate-50 dark:border-slate-950" />

                                                <div className="mb-3 flex items-center gap-3">
                                                    <h4 className="text-[13px] font-bold text-slate-700 dark:text-slate-300">
                                                        {scene.scene_id}
                                                    </h4>
                                                </div>

                                                <div className="space-y-3">
                                                    {scene.segments.map(segment => (
                                                        <div key={segment.segment_id} className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-3 shadow-sm hover:shadow-md hover:border-indigo-300 dark:hover:border-indigo-500/50 transition-all group">
                                                            <div className="flex items-center gap-4 mb-3">
                                                                <div className="w-7 h-6 rounded flex items-center justify-center bg-slate-100 dark:bg-slate-800 text-[10px] font-bold text-slate-500">
                                                                    S{segment.segment_index}
                                                                </div>
                                                                <span className="text-[11px] font-bold text-slate-400">
                                                                    {segment.duration.toFixed(2)}s
                                                                </span>
                                                            </div>

                                                            {/* Task Pipeline Visualizer */}
                                                            <div className="flex flex-wrap items-center gap-1.5 font-mono">
                                                                <TaskBadge active={segment.tasks.need_audio} icon={Mic} label="AUDIO" />
                                                                <span className="text-slate-300 dark:text-slate-700">→</span>
                                                                <TaskBadge active={segment.tasks.need_image} icon={ImageIcon} label="First Frame" />
                                                                <span className="text-slate-300 dark:text-slate-700">→</span>
                                                                <TaskBadge active={segment.tasks.need_video} icon={Clapperboard} label="VIDEO" />
                                                                <span className="text-slate-300 dark:text-slate-700">→</span>
                                                                <TaskBadge active={segment.tasks.need_lipsync} icon={AudioLines} label="SYNC" />
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>

                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ))}

                            <div className="pt-8 pb-12 flex justify-center">
                                <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 text-xs font-bold text-slate-500">
                                    <CheckCircle2 size={14} className="text-slate-400" /> End of Plan
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </motion.div>
        </div>
    );
}
