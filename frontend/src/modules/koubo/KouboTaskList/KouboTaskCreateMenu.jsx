export default function KouboTaskCreateMenu(props) {
  return (
    <div class="koubo-task-list-create-actions">
      <button type="button" onClick={props.onVideo}>视频分析</button>
      <button type="button" class="secondary" onClick={props.onTalkingHead}>人物口播</button>
      <button type="button" class="secondary" onClick={props.onScript}>脚本生成</button>
      <button type="button" class="secondary" onClick={props.onDanceMimic}>动作模拟</button>
    </div>
  );
}
