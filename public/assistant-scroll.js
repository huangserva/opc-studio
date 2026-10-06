/* Follow new output only while the reader stays at the end of the conversation. */
const assistantScroll={followLatest:true,anchor:null};
const assistantScrollBox=document.querySelector('#assistant-messages');
const assistantMessageList=document.querySelector('#assistant-message-list');
const assistantJumpButton=document.querySelector('#assistant-jump-latest');
function assistantNearBottom(){
  return assistantScrollBox.scrollHeight-assistantScrollBox.scrollTop-assistantScrollBox.clientHeight<=24;
}
function captureAssistantScroll(){
  const box=assistantScrollBox,edge=box.getBoundingClientRect().top;
  const first=[...assistantMessageList.children].find(node=>node.dataset.chatTurn&&node.getBoundingClientRect().bottom>edge);
  return {top:box.scrollTop,id:first?.dataset.chatTurn,offset:first?first.getBoundingClientRect().top-edge:0};
}
function updateAssistantJump(){
  assistantJumpButton.hidden=assistantScroll.followLatest||assistantNearBottom()||assistantScrollBox.clientHeight===0;
}
function restoreAssistantScroll(position=assistantScroll.anchor){
  const box=assistantScrollBox;
  if(!box.clientHeight)return;
  let top=position?.top||0;
  if(assistantScroll.followLatest)top=box.scrollHeight;
  else if(position?.id){
    const node=[...assistantMessageList.children].find(node=>node.dataset.chatTurn===position.id);
    if(node)top=box.scrollTop+node.getBoundingClientRect().top-box.getBoundingClientRect().top-position.offset;
  }
  // Polling must not leave a smooth scroll running after the user starts reading.
  box.scrollTo({top,behavior:'instant'});
  assistantScroll.anchor=captureAssistantScroll();updateAssistantJump();
}
function jumpToLatestAssistant(){
  assistantScroll.followLatest=true;restoreAssistantScroll();
}
assistantJumpButton.addEventListener('click',jumpToLatestAssistant);
assistantScrollBox.addEventListener('scroll',()=>{
  if(!assistantScrollBox.clientHeight)return;
  assistantScroll.followLatest=assistantNearBottom();
  assistantScroll.anchor=captureAssistantScroll();updateAssistantJump();
},{passive:true});
// Image/video loading and composer resizing can change layout after a chat redraw.
const assistantScrollObserver=new ResizeObserver(()=>restoreAssistantScroll());
assistantScrollObserver.observe(assistantScrollBox);
assistantScrollObserver.observe(assistantMessageList);
