import { Show, Suspense, lazy } from "solid-js";
import StoryboardIcon from "../modules/koubo/shared/StoryboardIcon.jsx";
import { ModelConfigActions, ModelConfigProvider } from "../../../ModelConfig/frontend/src/ModelConfigModule";
import DebugConsole, { debugConsoleHeight } from "../debug/DebugConsole.jsx";
import SettingsDrawers from "./SettingsDrawers.jsx";
import AppRightSidebar from "./AppRightSidebar.jsx";
import ShellDialogs from "./ShellDialogs.jsx";
import AuthGate from "./AuthGate.jsx";
import {
    dispatchWindowEvent,
    SidebarToggleIcon,
    ConnectionIcon,
    AudioWaveIcon,
    FileIcon,
    MediaLibraryIcon,
    BillingIcon,
    LogoutIcon,
} from "./appShellUtils.jsx";

const AnalysisV1Module = lazy(() => import("../modules/koubo/AnalysisV1/AnalysisV1Module.jsx"));
const KouboTaskListPage = lazy(() => import("../modules/koubo/KouboTaskList/index.jsx"));
const MediaLibraryModule = lazy(() => import("../modules/mediaLibrary/MediaLibraryModule.jsx"));
const DanceMimicV1Module = lazy(() => import("../modules/koubo/DanceMimicV1/DanceMimicV1Module.jsx"));
const DanceMimicV1MediaSidebar = lazy(() => import("../modules/koubo/DanceMimicV1/DanceMimicV1Module.jsx").then((mod) => ({ default: mod.DanceMimicV1MediaSidebar })));
const TalkingHeadV1Module = lazy(() => import("../modules/koubo/TalkingHeadV1/TalkingHeadV1Module.jsx"));
const TalkingHeadV1MediaSidebar = lazy(() => import("../modules/koubo/TalkingHeadV1/TalkingHeadV1Module.jsx").then((mod) => ({ default: mod.TalkingHeadV1MediaSidebar })));
const KouboStoryBoardModule = lazy(() => import("../modules/koubo/KouboStoryBoardModule.jsx"));
const UploadAssetLibraryPage = lazy(() => import("../modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx"));
const AnalysisV1MediaSidebar = lazy(() => import("../modules/koubo/AnalysisV1/components/AnalysisV1DialogueView.jsx").then((mod) => ({ default: mod.AnalysisV1MediaSidebar })));
const MeteringPage = lazy(() => import("../pages/MeteringPage.jsx"));
const ConnectionPage = lazy(() => import("../pages/ConnectionPage.jsx"));

export default function OpenCrewShellView(props) {
    const {
        summary,
        setSummary,
        activeNav,
        setActiveNav,
        busyStep,
        setBusyStep,
        error,
        setError,
        authState,
        setAuthState,
        authPassword,
        setAuthPassword,
        authError,
        setAuthError,
        authBusy,
        setAuthBusy,
        opencodeBaseUrl,
        setOpencodeBaseUrl,
        opencodeUsername,
        setOpencodeUsername,
        opencodePassword,
        setOpencodePassword,
        opencodeDirty,
        setOpencodeDirty,
        opencodeCandidates,
        setOpencodeCandidates,
        discoverAttempted,
        setDiscoverAttempted,
        publishUrl,
        setPublishUrl,
        publishDirty,
        setPublishDirty,
        publishPreview,
        setPublishPreview,
        publishChecks,
        setPublishChecks,
        skills,
        setSkills,
        tasks,
        setTasks,
        taskLogs,
        setTaskLogs,
        activeTaskIds,
        setActiveTaskIds,
        editorKind,
        setEditorKind,
        editorContent,
        setEditorContent,
        rightSidebarWidth,
        setRightSidebarWidth,
        rightResizeState,
        setRightResizeState,
        envDialog,
        setEnvDialog,
        runDialog,
        setRunDialog,
        asrDialog,
        setAsrDialog,
        asrModels,
        setAsrModels,
        asrConfig,
        setAsrConfig,
        mediaDialog,
        setMediaDialog,
        mediaConfig,
        setMediaConfig,
        mediaApiKeys,
        setMediaApiKeys,
        mihomoConfig,
        setMihomoConfig,
        mihomoSubscriptionUrl,
        setMihomoSubscriptionUrl,
        mihomoBusy,
        setMihomoBusy,
        mihomoError,
        setMihomoError,
        mihomoTestResult,
        setMihomoTestResult,
        mediaPriceListOpen,
        setMediaPriceListOpen,
        mediaUnitPriceOpen,
        setMediaUnitPriceOpen,
        usdCnyRate,
        setUsdCnyRate,
        meteringReport,
        setMeteringReport,
        meteringTab,
        setMeteringTab,
        meteringTaskId,
        setMeteringTaskId,
        meteringAttemptScope,
        setMeteringAttemptScope,
        meteringTaskReport,
        setMeteringTaskReport,
        meteringDays,
        setMeteringDays,
        meteringBusy,
        setMeteringBusy,
        meteringTaskBusy,
        setMeteringTaskBusy,
        meteringError,
        setMeteringError,
        connectionTests,
        setConnectionTests,
        sessionItems,
        setSessionItems,
        selectedSessionId,
        setSelectedSessionId,
        sessionTaskSummary,
        setSessionTaskSummary,
        routeHash,
        setRouteHash,
        publishGuideOpen,
        setPublishGuideOpen,
        analysisV1MediaItem,
        setAnalysisV1MediaItem,
        danceMimicMediaItem,
        setDanceMimicMediaItem,
        talkingHeadMediaItem,
        setTalkingHeadMediaItem,
        kouboStoryBoardSidebar,
        setKouboStoryBoardSidebar,
        navCollapsed,
        setNavCollapsed,
        mediaAgentDrag,
        setMediaAgentDrag,
        initialMeteringRoute,
        events,
        state,
        npcState,
        publishState,
        sessionState,
        npcVerified,
        publishStepStatus,
        publishMessage,
        publishLastError,
        publishHasIssue,
        canManageConnection,
        canViewMetering,
        roleAccess,
        navAllowed,
        statusCanManageConnection,
        statusCanViewMetering,
        isRetiredNavHash,
        goToBusinessHome,
        applyRoleRoute,
        syncActiveNavFromHash,
        publishCheckGroups,
        selectedSession,
        npcStepStatus,
        npcResultMessage,
        npcMessage,
        npcResultVariant,
        refresh,
        loadMihomoConfig,
        saveMihomoConfig,
        testMihomoConfig,
        loadMeteringReport,
        loadMeteringTaskReport,
        selectMeteringTask,
        loadInitialData,
        submitAuth,
        loadAsrConfig,
        openAsrDialog,
        selectedAsrModel,
        asrProviderCards,
        testStateKey,
        resetConnectionTest,
        updateConnectionTest,
        connectionTestState,
        updateAsrModel,
        activateAsrProviderForInput,
        saveAsrConfig,
        mediaDialogTitle,
        mediaDialogKindLabel,
        loadUsdCnyRate,
        mediaPriceRanking,
        lipsyncPriceComparisonRows,
        mediaUnitPriceRows,
        setMediaAgentPoolElement,
        mediaAgentAliases,
        mediaSupportsAgentAliases,
        mediaAgentKindLabel,
        mediaProviderLabel,
        mediaModelLabel,
        mediaCredentialFields,
        mediaCredentialKey,
        hasMediaKeyInput,
        mediaProviderApiKeyPayload,
        selectedMediaModel,
        selectedMediaModelPriceText,
        defaultAgentAlias,
        setMediaAgentAliases,
        addMediaAgentAlias,
        updateMediaAgentAlias,
        removeMediaAgentAlias,
        removeMediaAgentDragListeners,
        finishMediaAgentDrag,
        startMediaAgentDrag,
        handleMediaAgentDrop,
        openMediaDialog,
        setActiveMediaProvider,
        updateMediaProviderModel,
        saveMediaConfig,
        runMediaConnectionTest,
        runAsrConnectionTest,
        renderConnectionTestControl,
        saveOpenCodeConfig,
        loadSessions,
        loadSkill,
        loadSkills,
        loadNpcConfig,
        loadPublishConfig,
        currentPublishUrl,
        savePublishConfig,
        refreshPublishTask,
        startPublishValidationTask,
        currentRunConfig,
        saveNpcConfig,
        refreshTask,
        startNpcTask,
        refreshAnyTask,
        runAction,
        runDiscover,
        runNpcDetect,
        openRunDialog,
        reconnectNpcService,
        openEditor,
        saveEditor,
        restoreSkill,
        loginToOpenCode,
        startRightResize,
        authReady,
        logout,
        handleMediaAgentPointerMove,
        handleMediaAgentPointerUp
    } = props;
    return (<ModelConfigProvider>
    <Show when={authReady()} fallback={<AuthGate
      authState={authState}
      authPassword={authPassword}
      authError={authError}
      authBusy={authBusy}
      setAuthPassword={setAuthPassword}
      submitAuth={submitAuth}
    />}>
    <div class={`shell ${navCollapsed() ? "is-nav-collapsed" : ""} ${activeNav() === "metering" ? "is-metering" : ""} ${activeNav() === "koubo-asset-library" || activeNav() === "media-library" ? "is-full-center" : ""}`} style={{ "--debug-console-height": authState().debug_console_enabled ? debugConsoleHeight() : "0px", "--right-sidebar-width": `${rightSidebarWidth()}px` }}>
      <aside class="left">
        <div class="brand-row">
          <div class="brand">OpenCrew</div>
          <button class="nav-collapse-toggle" type="button" title={navCollapsed() ? "展开导航" : "收起导航"} aria-label={navCollapsed() ? "展开导航" : "收起导航"} onClick={() => setNavCollapsed((value) => !value)}><SidebarToggleIcon collapsed={navCollapsed()} /></button>
        </div>
        <nav class="nav">
          <Show when={canManageConnection()}>
          <button title="Connection" class={`nav-item ${activeNav() === "connection" ? "active" : ""}`} onClick={() => { setActiveNav("connection"); if (window.location.hash.startsWith("#/media-library") || window.location.hash.startsWith("#/analysis-v1") || window.location.hash.startsWith("#/koubo-tasks") || window.location.hash.startsWith("#/koubo-storyboard") || window.location.hash.startsWith("#/koubo-asset-library") || window.location.hash.startsWith("#/metering"))
        window.location.hash = ""; }}><ConnectionIcon /><span>Connection</span></button>
          </Show>
          <button title="素材库" class={`nav-item ${activeNav() === "media-library" ? "active" : ""}`} onClick={() => { setActiveNav("media-library"); window.location.hash = "#/media-library"; }}><MediaLibraryIcon /><span>素材库</span></button>
          <button title="任务列表（口播）" class={`nav-item ${activeNav() === "koubo-task-list" || activeNav() === "dance-mimic" || activeNav() === "talking-head" ? "active" : ""}`} onClick={() => { setActiveNav("koubo-task-list"); window.location.hash = "#/koubo-tasks"; }}><FileIcon /><span>任务列表（口播）</span></button>
          <button title="视频分析（口播）" class={`nav-item ${activeNav() === "analysis-v1" ? "active" : ""}`} onClick={() => { setActiveNav("analysis-v1"); window.location.hash = "#/analysis-v1/tasks"; }}><AudioWaveIcon /><span>视频分析（口播）</span></button>
          <button title="故事版（口播）" class={`nav-item ${activeNav() === "koubo-storyboard" || activeNav() === "koubo-asset-library" ? "active" : ""}`} onClick={() => { setActiveNav("koubo-storyboard"); window.location.hash = "#/koubo-storyboard/tasks"; }}><StoryboardIcon /><span>故事版（口播）</span></button>
          <Show when={canViewMetering()}>
          <button title="本地计费" class={`nav-item ${activeNav() === "metering" ? "active" : ""}`} onClick={() => { setActiveNav("metering"); window.location.hash = "#/metering"; }}><BillingIcon /><span>计费</span></button>
          </Show>
        </nav>
        <div class="left-foot">
          <Show when={authState().enabled}>
            <button class="nav-item logout" type="button" title="Logout" onClick={() => void logout()}><LogoutIcon /><span>Logout</span></button>
          </Show>
        </div>
      </aside>

      <main class="center">
        <Show when={activeNav() !== "media-library" && activeNav() !== "koubo-storyboard" && activeNav() !== "koubo-asset-library" && activeNav() !== "koubo-task-list" && activeNav() !== "dance-mimic" && activeNav() !== "talking-head"}>
        <header class="center-header">
          <h1>{activeNav() === "connection" ? "Connection" : activeNav() === "analysis-v1" ? "视频分析（口播）" : activeNav() === "metering" ? "本地计费" : "视频分析（口播）"}</h1>
          <Show when={activeNav() === "connection"}>
            <div class="center-header-actions">
              <ModelConfigActions />
            </div>
          </Show>
          <Show when={activeNav() === "analysis-v1"}>
            <div class="center-header-actions">
              <button class="secondary" type="button" onClick={() => dispatchWindowEvent("analysis-v1:task-list")}>Task List</button>
              <button class="secondary" type="button" onClick={() => dispatchWindowEvent("analysis-v1:new-task")}>New Task</button>
            </div>
          </Show>
        </header>
        </Show>

        <Show when={error()}>
          <div class="banner bad">{error()}</div>
        </Show>

        <Suspense fallback={<div class="banner">Loading...</div>}>
        <Show when={activeNav() === "connection"} fallback={activeNav() === "media-library" ? <MediaLibraryModule routeHash={routeHash()} /> : activeNav() === "analysis-v1" ? <AnalysisV1Module routeHash={routeHash()} roleAccess={roleAccess()} onMediaItemChange={setAnalysisV1MediaItem}/> : activeNav() === "koubo-task-list" ? <KouboTaskListPage routeHash={routeHash()} /> : activeNav() === "dance-mimic" ? <DanceMimicV1Module routeHash={routeHash()} onMediaItemChange={setDanceMimicMediaItem} /> : activeNav() === "talking-head" ? <TalkingHeadV1Module routeHash={routeHash()} onMediaItemChange={setTalkingHeadMediaItem} /> : activeNav() === "koubo-storyboard" ? <KouboStoryBoardModule routeHash={routeHash()} roleAccess={roleAccess()} onSidebarChange={setKouboStoryBoardSidebar}/> : activeNav() === "koubo-asset-library" ? <UploadAssetLibraryPage routeHash={routeHash()} /> : activeNav() === "metering" ? <MeteringPage
          meteringReport={meteringReport}
          meteringTaskReport={meteringTaskReport}
          meteringDays={meteringDays}
          setMeteringDays={setMeteringDays}
          meteringBusy={meteringBusy}
          loadMeteringReport={loadMeteringReport}
          meteringTaskId={meteringTaskId}
          selectMeteringTask={selectMeteringTask}
          meteringAttemptScope={meteringAttemptScope}
          loadMeteringTaskReport={loadMeteringTaskReport}
          meteringError={meteringError}
          meteringTab={meteringTab}
          setMeteringTab={setMeteringTab}
        /> : <AnalysisV1Module routeHash={routeHash()} roleAccess={roleAccess()} onMediaItemChange={setAnalysisV1MediaItem}/>}>
          <ConnectionPage
            state={state}
            busyStep={busyStep}
            runAction={runAction}
            runDiscover={runDiscover}
            opencodeBaseUrl={opencodeBaseUrl}
            setOpencodeBaseUrl={setOpencodeBaseUrl}
            setOpencodeDirty={setOpencodeDirty}
            opencodeUsername={opencodeUsername}
            setOpencodeUsername={setOpencodeUsername}
            opencodePassword={opencodePassword}
            setOpencodePassword={setOpencodePassword}
            opencodeCandidates={opencodeCandidates}
            saveOpenCodeConfig={saveOpenCodeConfig}
            loginToOpenCode={loginToOpenCode}
            discoverAttempted={discoverAttempted}
            npcStepStatus={npcStepStatus}
            startNpcTask={startNpcTask}
            openEditor={openEditor}
            openRunDialog={openRunDialog}
            npcState={npcState}
            runDialog={runDialog}
            npcResultVariant={npcResultVariant}
            npcMessage={npcMessage}
            npcResultMessage={npcResultMessage}
            publishStepStatus={publishStepStatus}
            currentPublishUrl={currentPublishUrl}
            savePublishConfig={savePublishConfig}
            startPublishValidationTask={startPublishValidationTask}
            npcVerified={npcVerified}
            publishUrl={publishUrl}
            setPublishUrl={setPublishUrl}
            setPublishDirty={setPublishDirty}
            publishPreview={publishPreview}
            publishHasIssue={publishHasIssue}
            publishMessage={publishMessage}
            publishLastError={publishLastError}
            mihomoConfig={mihomoConfig}
            mihomoBusy={mihomoBusy}
            testMihomoConfig={testMihomoConfig}
            saveMihomoConfig={saveMihomoConfig}
            mihomoSubscriptionUrl={mihomoSubscriptionUrl}
            setMihomoSubscriptionUrl={setMihomoSubscriptionUrl}
            mihomoTestResult={mihomoTestResult}
            mihomoError={mihomoError}
          />
        </Show>
        </Suspense>
      </main>

      <SettingsDrawers
        asrDialog={asrDialog}
        setAsrDialog={setAsrDialog}
        asrProviderCards={asrProviderCards}
        asrConfig={asrConfig}
        updateAsrModel={updateAsrModel}
        renderConnectionTestControl={renderConnectionTestControl}
        runAsrConnectionTest={runAsrConnectionTest}
        activateAsrProviderForInput={activateAsrProviderForInput}
        setAsrConfig={setAsrConfig}
        saveAsrConfig={saveAsrConfig}
        mediaDialog={mediaDialog}
        setMediaDialog={setMediaDialog}
        mediaDialogTitle={mediaDialogTitle}
        setMediaUnitPriceOpen={setMediaUnitPriceOpen}
        setMediaPriceListOpen={setMediaPriceListOpen}
        mediaPriceListOpen={mediaPriceListOpen}
        usdCnyRate={usdCnyRate}
        mediaPriceRanking={mediaPriceRanking}
        mediaDialogKindLabel={mediaDialogKindLabel}
        mediaUnitPriceOpen={mediaUnitPriceOpen}
        mediaUnitPriceRows={mediaUnitPriceRows}
        mediaConfig={mediaConfig}
        setActiveMediaProvider={setActiveMediaProvider}
        hasMediaKeyInput={hasMediaKeyInput}
        runMediaConnectionTest={runMediaConnectionTest}
        selectedMediaModelPriceText={selectedMediaModelPriceText}
        mediaCredentialFields={mediaCredentialFields}
        mediaApiKeys={mediaApiKeys}
        setMediaApiKeys={setMediaApiKeys}
        mediaCredentialKey={mediaCredentialKey}
        startMediaAgentDrag={startMediaAgentDrag}
        updateMediaProviderModel={updateMediaProviderModel}
        mediaSupportsAgentAliases={mediaSupportsAgentAliases}
        setMediaAgentPoolElement={setMediaAgentPoolElement}
        handleMediaAgentDrop={handleMediaAgentDrop}
        mediaAgentKindLabel={mediaAgentKindLabel}
        mediaAgentAliases={mediaAgentAliases}
        updateMediaAgentAlias={updateMediaAgentAlias}
        mediaProviderLabel={mediaProviderLabel}
        mediaModelLabel={mediaModelLabel}
        removeMediaAgentAlias={removeMediaAgentAlias}
        lipsyncPriceComparisonRows={lipsyncPriceComparisonRows}
        mediaAgentDrag={mediaAgentDrag}
        saveMediaConfig={saveMediaConfig}
      />

      <Suspense fallback={null}>
      <AppRightSidebar
        activeNav={activeNav}
        startRightResize={startRightResize}
        AnalysisV1MediaSidebar={AnalysisV1MediaSidebar}
        analysisV1MediaItem={analysisV1MediaItem}
        DanceMimicV1MediaSidebar={DanceMimicV1MediaSidebar}
        danceMimicMediaItem={danceMimicMediaItem}
        TalkingHeadV1MediaSidebar={TalkingHeadV1MediaSidebar}
        talkingHeadMediaItem={talkingHeadMediaItem}
        kouboStoryBoardSidebar={kouboStoryBoardSidebar}
        meteringReport={meteringReport}
        meteringDays={meteringDays}
        sessionTaskSummary={sessionTaskSummary}
        selectedSession={selectedSession}
        state={state}
        npcStepStatus={npcStepStatus}
        publishStepStatus={publishStepStatus}
      />
      </Suspense>

      <Show when={authState().debug_console_enabled}>
        <DebugConsole />
      </Show>

      <ShellDialogs
        runDialog={runDialog}
        setRunDialog={setRunDialog}
        busyStep={busyStep}
        saveNpcConfig={saveNpcConfig}
        reconnectNpcService={reconnectNpcService}
        editorKind={editorKind}
        setEditorKind={setEditorKind}
        skills={skills}
        editorContent={editorContent}
        setEditorContent={setEditorContent}
        saveEditor={saveEditor}
        restoreSkill={restoreSkill}
        publishGuideOpen={publishGuideOpen}
        setPublishGuideOpen={setPublishGuideOpen}
        tasks={tasks}
        publishPreview={publishPreview}
        taskLogs={taskLogs}
        publishCheckGroups={publishCheckGroups}
        envDialog={envDialog}
        setEnvDialog={setEnvDialog}
      />
    </div>
    </Show>
    </ModelConfigProvider>);
}
