const lab={kind:'video',initialized:false,selected:null,submitting:false,uploading:0,busy:false,timer:null,request:null,compare:new Set(),selection:new Set(),trash:false,historySignature:null,previewKey:null,models:[],jobs:[]};
const labStatuses={connecting:'连接中',queued:'排队中',running:'正在生成',downloading:'保存中',reconnecting:'重连中',uncertain:'待核实',cancelled:'已停止',done:'已完成',error:'失败'};
const labStorage='studio-generation-lab-test-v2-video';

// 全局标记：用户是否主动清空了参考图（防止后台自动借尸还魂回填）
window.labUserClearedRefs = false;

const DRAMA_PRESETS = {
  island: {
    name: '🔥 极客荒岛·篝火守夜 (AICG3D原装编译器·同空间一镜到底60s)',
    skill_id: 'short-scene-director-lite',
    storyIdea: '黄昏礁石海滩的篝火旁，年轻极客创业者添柴稳住篝火后打开金属工具箱取出改装信号终端，精准调频并起身拉出天线向轨道信标发射求救坐标，成功收到回执后安心坐回篝火旁拨火守夜。',
    characterPrompt: '电影级真实主角半身定妆照，一位极具硅谷极客气质的年轻科技创业者（30岁左右，短卷发，深邃自信的眼神，身穿深灰色户外战术冲锋衣），背后是黄昏礁石海滩与温暖篝火微光虚化背景，写实摄影质感，自然皮肤肌理与毛孔质感，8k超清',
    acts: [
      {
        id: 1,
        title: '第一幕·添柴开箱 (0-15s)',
        subtitle: '黄昏篝火旁同空间 · 段尾触锁扣钩子',
        prompt: `【第1段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，数字电影机浅景深成像，黄昏海滩礁石营地同一空间。中景平视机位，<Picture 1> 的年轻男性身穿深灰色户外冲锋衣，半蹲在礁石滩一簇跳跃的暖橙色篝火右侧，背景是深蓝色暮色海湾与湿润礁石，右下方暖橙火光与左上方冷蓝海暮光均匀照亮人物面部轮廓。
0.0～1.6秒：男性半蹲在篝火旁，右手将一根枯木枝轻轻添进火堆，火星随之微微跃起，海风轻拂他的额前短发。
1.6～3.6秒：男性拍掉双手手心的细碎木屑，双手自然搭在右膝上，镜头做极小幅度的平稳推进。
3.6～5.0秒：男性缓缓抬起头望向镜头前方的海面方向，肩膀轻微起伏完成吸气，画面停在他半蹲凝视、准备开口说话的稳定瞬间。

overall_soundscape: 篝火木柴持续噼啪燃烧声，傍晚海浪轻拍礁石的规律低鸣，手掌拍落木屑的细微摩擦声。

non_diegetic_music: 低沉克制的大提琴环境铺底缓慢进入，音量轻柔。

【第2段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定凝视机位，<Picture 1> 的年轻男性仍穿着深灰色户外冲锋衣半蹲在篝火右侧原位，右下方暖橙色篝火主光与背后深蓝海面环境光与前序画面一致，面部五官清晰锐利。
0.0～0.8秒：男性目光平视镜头前方，眼神沉稳，胸口轻微吸气进入说话状态。
0.8～3.6秒：男性 (S1) says: <d>[Chinese]流落到这座荒岛第三天，篝火终于稳住了。</d>
3.6～5.0秒：男性闭上嘴唇，视线向下移向身旁平整礁石上的银灰色金属工具箱，右手缓慢伸出，画面停在右手指尖刚触碰到工具箱金属锁扣的瞬间。

overall_soundscape: 篝火持续温暖噼啪声，海风拂过冲锋衣领口的轻微声响，男性清晰的呼吸声。

non_diegetic_music: 低沉大提琴铺底平稳延续。

【第3段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视略带俯角机位，<Picture 1> 的年轻男性半蹲在篝火与礁石旁，右手指尖正搭在金属工具箱锁扣上，暖橙色篝火与冷蓝海暮光色温曝光稳定。
0.0～1.5秒：男性右手拇指向上拨开金属锁扣，顺势轻轻掀开工具箱顶盖。
1.5～3.6秒：男性双手从箱内稳稳取出一台改装过的便携式太阳能信号终端，将其平托在胸前掌心。
3.6～5.0秒：终端屏幕亮起一抹柔和的冰蓝色微光，映在男性下颌与鼻梁上，他微微低头凝视屏幕，右手拇指悬停在终端侧边按键上方准备按下。

overall_soundscape: 金属锁扣清脆弹开声，手指托起电子设备的轻微摩擦声，篝火噼啪声与海浪声持续。

non_diegetic_music: 柔和的电子脉冲弦乐轻声汇入大提琴铺底。`
      },
      {
        id: 2,
        title: '第二幕·调频起身 (15-30s)',
        subtitle: '篝火旁同空间 · 旋钮调频接起身拉天线',
        prompt: `【第4段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定机位，<Picture 1> 的年轻男性半蹲在篝火旁，双手在胸前捧着亮起冰蓝微光的信号终端，暖橙火光与屏幕冷光在面部形成冷暖交融层次。
0.0～0.8秒：男性右手拇指按下侧边按键，眼神专注地抬眼平视前方，轻微吸气。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]电量只够发一次脉冲，频率得调到最准。</d>
3.7～5.0秒：男性闭上嘴巴，目光重新落回终端顶部，右手食指与拇指稳稳捏住终端顶部的金属调频旋钮，画面停在手指即将发力拧动旋钮的瞬间。

overall_soundscape: 按键轻响一声，篝火木柴轻微爆裂声，远处平稳的海浪环境音。

non_diegetic_music: 低沉弦乐保持慢速张力。

【第5段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视机位，<Picture 1> 的年轻男性半蹲在篝火旁，左手托着信号终端，右手食指与拇指正捏在终端顶部的金属旋钮上，面部与手部对焦清晰。
0.0～1.8秒：男性右手手指缓慢而精准地顺时针拧动调频旋钮两格，眉头微敛，眼神紧盯屏幕波形。
1.8～3.6秒：终端侧面的指示灯由冰蓝色转为稳定的翠绿色微光，男性紧绷的眉宇顿时舒展开来，嘴角浮现一丝欣慰弧度。
3.6～5.0秒：男性双手握稳终端，左脚脚掌在礁石地面发力向下蹬，身体重心向上抬起，画面停在他从半蹲向站立过渡到一半的动态瞬间。

overall_soundscape: 金属旋钮细腻的刻度咔哒声，设备锁定频率的短促轻微蜂鸣，衣料随起身动作的摩擦声。

non_diegetic_music: 弦乐音高轻微上扬，营造希望初现的氛围。

【第6段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。中近景平视机位，镜头随人物起身做轻微向上摇移，<Picture 1> 的年轻男性正站在篝火右侧，身穿深灰色户外冲锋衣，暖橙色火光从下方映照衣摆，深蓝海湾暮色作背景。
0.0～1.5秒：男性顺着上一段起身的动量完全站直身体，双脚稳稳立在礁石滩上，双手将信号终端举至胸前高度。
1.5～3.6秒：男性右手捏住终端顶端的金属折叠天线，流畅地向上拉出完整一节，天线尖端反射出篝火微光。
3.6～5.0秒：男性抬头望向斜上方开阔的海湾夜空，胸廓微扩吸气，画面停在他双手托举终端、准备开口的稳定姿态。

overall_soundscape: 金属天线抽出的清脆滑动声，海风吹动冲锋衣下摆的呼啸声，篝火持续燃烧声。

non_diegetic_music: 温暖的钢琴单音伴随弦乐缓慢铺开。`
      },
      {
        id: 3,
        title: '第三幕·脉冲发信 (30-45s)',
        subtitle: '篝火旁同空间 · 按键发射接放松落座',
        prompt: `【第7段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定机位，<Picture 1> 的年轻男性站立在篝火旁，双手在胸前托着展开天线的信号终端，面部受暖橙火光与翠绿指示灯双重映照，神情笃定。
0.0～0.8秒：男性目光从夜空收回，坚定地平视镜头前方的终端屏幕，呼吸平稳。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]中继信标已经锁定轨道，现在开始上传坐标。</d>
3.7～5.0秒：男性闭上嘴巴，右手大拇指移至终端中央的发射按钮上并果断向下按压，画面停在拇指按住发射键、翠绿光芒映亮指尖的瞬间。

overall_soundscape: 篝火噼啪声，海浪拍岸声，拇指按下机械发射键的清脆咔哒声。

non_diegetic_music: 钢琴与弦乐平稳推进，情绪凝聚。

【第8段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视机位，<Picture 1> 的年轻男性站立在篝火旁，双手托着信号终端，右手拇指仍按在发射键上，光影色温与前序镜头一致。
0.0～1.8秒：终端顶部的翠绿色指示灯连续规律闪烁三次，男性屏住呼吸，目光紧紧锁住屏幕上传进度。
1.8～3.6秒：终端指示灯转为常亮绿光，男性紧绷的双肩瞬间自然下沉，胸口起伏长长舒出一口气，眼中浮现笑意。
3.6～5.0秒：男性右手拇指松开发射键，双臂开始将胸前的终端缓缓向下收拢，画面停在手臂下放一半的放松瞬间。

overall_soundscape: 电子终端清脆柔和的发送成功双音提示，男性长舒一口气的真实呼吸声，海风与篝火声持续。

non_diegetic_music: 温暖舒展的大提琴与钢琴和弦自然绽放。

【第9段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视机位，<Picture 1> 的年轻男性站立在篝火右侧，双手将信号终端自然垂捧在腰腹前，暖橙色篝火映亮他舒展温和的面庞。
0.0～0.8秒：男性眼神温和地看向镜头前方，嘴角带着笃定的微笑，轻轻吸气。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]信号发出去了，天亮之前一定会有回应。</d>
3.7～5.0秒：男性闭上嘴巴微微点头，双膝弯曲，身体重心平稳向下沉，画面停在他准备坐回篝火旁平整礁石的半蹲瞬间。

overall_soundscape: 篝火温暖的噼啪声，海浪轻柔拍岸声，冲锋衣随屈膝动作的轻微摩擦声。

non_diegetic_music: 舒缓温暖的钢琴旋律持续流淌。`
      },
      {
        id: 4,
        title: '第四幕·烤火守夜 (45-60s)',
        subtitle: '篝火旁同空间 · 落座烘火接拨火升火星',
        prompt: `【第10段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。中景平视机位，镜头随人物落座轻微下移，<Picture 1> 的年轻男性正坐在篝火右侧的平整礁石上，暖橙色篝火在画面左前方跳跃，背景是静谧的深蓝海湾。
0.0～1.6秒：男性稳稳坐定在礁石上，右手将信号终端轻轻放回身旁的金属工具箱内。
1.6～3.6秒：男性将双手自然伸向篝火上方轻轻烘烤取暖，跳跃的火光在他掌心与脸庞之间映出温暖的明暗变化。
3.6～5.0秒：男性双手收回搭在膝盖上，头部缓慢转向右侧深蓝色的海湾远方，画面停在他安静侧颜望海的瞬间。

overall_soundscape: 设备放入箱内的轻响，双手靠近火堆的细微摩擦声，篝火燃烧与海浪环境音。

non_diegetic_music: 大提琴与钢琴旋律温暖交织。

【第11段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。近景平视固定机位，<Picture 1> 的年轻男性坐在篝火右侧的礁石上，暖橙火光稳定勾勒出他的面部轮廓，服装与环境光影与前序镜头一致。
0.0～0.8秒：男性从右侧海面方向缓缓转回头看向镜头前方，眼神清澈从容，轻微吸气。
0.8～3.8秒：男性 (S1) says: <d>[Chinese]不管在代码里还是荒野里，活下去靠的都是耐心。</d>
3.8～5.0秒：男性闭上嘴巴，嘴角保留浅浅笑意，右手俯身拾起脚边一根细枯木枝，画面停在木枝尖端刚靠近篝火边缘的瞬间。

overall_soundscape: 篝火持续噼啪作响，远处柔和的海浪声，手指拾起枯枝的轻微声响。

non_diegetic_music: 钢琴旋律缓慢进入尾声前的温暖铺陈。

【第12段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，黄昏海滩礁石营地篝火旁。中景平视机位，镜头从近景开始极其缓慢平稳地向后拉远，<Picture 1> 的年轻男性坐在篝火右侧的礁石上，右手握着细木枝靠近火堆。
0.0～1.8秒：男性用手中的木枝轻轻拨动了一下篝火中心的柴薪，几簇明亮的暖金色火星顺势向深蓝色夜空轻盈升腾。
1.8～3.8秒：男性将木枝放回火堆旁，双手自然交叠在膝头，安静凝望着跳动的温暖火光，镜头持续平滑向后拉升展现礁石营地与海湾全景。
3.8～5.0秒：海风轻拂男性发梢，暖橙色篝火与辽阔深蓝暮色海面在画面中形成静谧平衡，画面平稳停留在他守候黎明的坐姿定格中。

overall_soundscape: 木枝拨动炭火的清脆噼啪声，火星升腾的细微声响，辽阔海湾的轻柔海浪与夜风声。

non_diegetic_music: 温暖的大提琴与钢琴尾音缓缓渐弱收束。`
      }
    ]
  },
  tushan: {
    name: '🏛️ 涂山仙境·如烟大帝与红果 (13号工作流原装连续剧60s)',
    skill_id: 'chinese-celestial-palace',
    storyIdea: '涂山仙境白玉平台上，左侧<Picture 1>白衣女师妹向右侧<Picture 2>蓝衣男大师兄询问：“大师兄，柳如烟到底是谁？”大师兄诧异调侃：“连如烟大帝你都不知道，看样子你都不看红果的吗？”师妹困惑追问：“是贵州六盘水的那个红果吗？”大师兄哭笑不得解释那是人间最火的短剧阁并递给她一枚微光玉简，师妹调侃师兄闭关三天原来是在刷短剧，两人全程在白玉平台保持双人同框对话并远眺云海仙境。',
    characterPrompt: '16:9古风奇幻电影定妆照，云雾缭绕的涂山仙境白玉平台，二十岁出头的古风仙侠清丽女弟子，身穿素白飘逸古风仙门长袍，发髻佩戴银色发冠，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官精致清晰锐利，数字电影摄影机成像，8k超清',
    characterPrompts: [
      '16:9古风奇幻电影定妆照，云雾缭绕的涂山仙境白玉平台，二十岁出头的古风仙侠清丽女弟子，身穿素白飘逸古风仙门长袍，发髻佩戴银色发冠，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官精致清晰锐利，数字电影摄影机成像，8k超清',
      '16:9古风奇幻电影定妆照，云雾缭绕的涂山仙境白玉平台，二十五岁的古风仙侠俊朗男大师兄，身穿淡青蓝色古风仙门长袍，束发银冠，神情温和带笑意，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官清晰锐利，数字电影摄影机成像，8k超清'
    ],
    defaultReferenceJobs: ['45faa90091eb474797fe33ac247e4803', 'dde6bb69bf2e466b8058b74ff60c5e43'],
    acts: [
      {
        id: 1,
        title: '第一幕·柳如烟之问 (0-15s)',
        subtitle: '13号工作流原装段1~3 · 双人同框锁定',
        prompt: `【第1段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，场景为云雾缭绕的涂山仙境白玉平台。开场中远景平视，<Picture 1>白衣年轻女子站在画面左侧，<Picture 2>蓝衣青年男子站在画面右侧，两人面对面站立于白玉平台之上。镜头做连续平滑推进至双人近景，两人面部五官均保持清晰锐利。主光来自斜上方，均匀打亮两人面部轮廓，环境薄雾缓慢流动。
0.0～1.8秒：中远景平视完整展现涂山仙境白玉平台，左侧<Picture 1>白衣女子与右侧<Picture 2>蓝衣男子相对而立，山间薄雾缓缓飘动。
1.8～3.4秒：镜头持续向前平滑推进至双人同框近景，左侧<Picture 1>白衣女子眉眼带着疑惑神态望向右侧<Picture 2>蓝衣男子，发丝随微风轻微晃动。
3.4～5.0秒：镜头保持双人同框近景固定，两人身体站位保持不变，画面停留在左侧<Picture 1>白衣女子嘴唇微启准备发问的稳定状态。

overall_soundscape: 山间持续轻柔风声，远处隐约传来仙灵环境回响，布料被微风轻轻拂动摩擦声。

non_diegetic_music: N/A

【第2段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景镜头，左侧<Picture 1>白衣年轻女子正对右侧<Picture 2>蓝衣青年男子，二人站在平台原地不动，服装样貌与上一段一致，两人面部持续清晰对焦。镜头为固定凝视机位不做位移。主光依旧斜上方照射，面部光影、色温和曝光保持统一。
0.0～0.7秒：左侧<Picture 1>白衣女子眉头微微蹙起，嘴唇微张，身体小幅前倾，眼神充满疑惑望向右侧<Picture 2>蓝衣男子，肩膀轻微起伏完成吸气。
0.7～3.5秒：清脆年轻女子 (S1) says: <d>[Chinese]大师兄，柳如烟到底是谁？</d>
3.5～5.0秒：左侧<Picture 1>白衣女子闭上嘴巴，头部姿态不变，依旧带着疑惑看向右侧<Picture 2>蓝衣男子，等待对方作答，两人维持稳定的双人同框姿态。

overall_soundscape: 山间轻柔风声持续，衣物布料轻微摩擦，女子细微呼吸声。

non_diegetic_music: N/A

【第3段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子面对面站立，两人站位不变，面部全程清晰对焦，镜头保持固定凝视机位。主光斜上方，光影色调曝光延续前序段落。
0.0～0.6秒：右侧<Picture 2>蓝衣男子眉毛向上扬起，露出略带诧异的神情，肩膀微抬吸气，左侧<Picture 1>白衣女子闭口专注聆听。
0.6～2.8秒：沉稳青年男子 (S2) says: <d>[Chinese]连如烟大帝你都不知道，看样子你都不看红果的吗？</d>
2.8～3.6秒：右侧<Picture 2>蓝衣男子闭合嘴唇，目光注视左侧<Picture 1>白衣女子，身体保持不动。
3.6～5.0秒：左侧<Picture 1>白衣女子头部微微歪向一侧，脸上疑惑感加重，嘴唇轻启准备回话，画面停留在双人同框、女子即将开口的稳定姿态。

overall_soundscape: 山间轻柔风声持续，衣料轻微晃动，两人平缓呼吸声。

non_diegetic_music: N/A`
      },
      {
        id: 2,
        title: '第二幕·红果与玉简 (15-30s)',
        subtitle: '13号工作流原装段4 + 双人同框递玉简',
        prompt: `【第4段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框中近景镜头，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子保持平台原地对峙站位，两人全身与面部均在画面内清晰可见，镜头固定凝视。主光斜上方，光影色温曝光不变。
0.0～0.8秒：左侧<Picture 1>白衣女子眼神困惑，眉头紧锁，身体轻微向前倾。
0.8～2.4秒：清脆年轻女子 (S1) says: <d>[Chinese]是贵州六盘水的那个红果吗？</d>
2.4～3.6秒：左侧<Picture 1>白衣女子闭上嘴，右侧<Picture 2>蓝衣男子神色瞬间转为哭笑不得，右手抬起食指指向女子额前虚点一下。
3.6～5.0秒：右侧<Picture 2>蓝衣男子右手悬在半空准备放下，左侧<Picture 1>白衣女子缩了缩脖子露出无辜神情，画面停留在双人同框对视的衔接瞬间。

overall_soundscape: 山间风声持续，衣袖抬起时的布料摩擦声，轻柔呼吸声。

non_diegetic_music: N/A

【第5段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子站在平台原位，严禁任何一人移出画面，主光斜上方均匀照亮两人面部。
0.0～0.7秒：右侧<Picture 2>蓝衣男子放下右手，无奈叹了一口气看向左侧<Picture 1>白衣女子，肩膀微抬吸气。
0.7～3.6秒：沉稳青年男子 (S2) says: <d>[Chinese]那是山下人间最火的短剧阁，连掌门都在熬夜看。</d>
3.6～5.0秒：右侧<Picture 2>蓝衣男子闭上嘴巴，右手探入左袖准备取出一枚玉简，左侧<Picture 1>白衣女子目光好奇地盯向他的袖口，停在双人同框的衔接瞬间。

overall_soundscape: 山间清风拂过，衣袖摩擦声，男子清晰的叹息与呼吸声。

non_diegetic_music: N/A

【第6段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子面对面站在原位，平台云雾与斜上方柔和主光保持不变。
0.0～1.5秒：右侧<Picture 2>蓝衣男子右手从袖中取出一枚泛着温润白光的玉简，递向左侧<Picture 1>白衣女子面前。
1.5～3.6秒：左侧<Picture 1>白衣女子双手接过发光玉简捧在胸前，低头端详玉简表面流转的光纹，右侧<Picture 2>蓝衣男子负手站立注视她。
3.6～5.0秒：左侧<Picture 1>白衣女子抬起头看向右侧<Picture 2>蓝衣男子，嘴角露出一丝恍然笑意，停在吸气准备发问的双人同框瞬间。

overall_soundscape: 手指交接玉简的温润轻响，山谷微风声，衣料轻微摩擦声。

non_diegetic_music: N/A`
      },
      {
        id: 3,
        title: '第三幕·闭关刷剧调侃 (30-45s)',
        subtitle: '双人同框固定机位 · 调侃闭关参悟',
        prompt: `【第7段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子手持玉简看向右侧<Picture 2>蓝衣青年男子，两人全程保持在画面左右两侧，面部清晰对焦。
0.0～0.8秒：左侧<Picture 1>白衣女子轻轻晃了晃手中的玉简，眼神带着几分促狭笑意，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S1) says: <d>[Chinese]原来师兄闭关三天，就是在偷偷刷短剧？</d>
3.6～5.0秒：左侧<Picture 1>白衣女子闭上嘴，眉眼弯起看着右侧<Picture 2>蓝衣男子，画面停在双人同框等待师兄辩解的稳定瞬间。

overall_soundscape: 轻柔风声，衣袂微动声，女子清浅笑意呼吸声。

non_diegetic_music: N/A

【第8段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子站在原位不变，主光斜上方柔和稳定。
0.0～0.7秒：右侧<Picture 2>蓝衣男子脸上闪过一丝窘迫，轻咳一声挺直腰板，左侧<Picture 1>白衣女子闭口微笑着注视他。
0.7～3.6秒：沉稳青年男子 (S2) says: <d>[Chinese]此言差矣，我这是在参悟人间的叙事之道。</d>
3.6～5.0秒：右侧<Picture 2>蓝衣男子闭上嘴，双手背在身后微微昂头，左侧<Picture 1>白衣女子把玉简妥帖收入袖中，画面停在双人同框的衔接姿态。

overall_soundscape: 男子轻微干咳声，衣袍拂动声，山间持续清风。

non_diegetic_music: N/A

【第9段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子面对面站在白玉栏杆旁，两人面部光影清晰自然。
0.0～0.8秒：左侧<Picture 1>白衣女子收好玉简后双手轻轻搭在身前，眼神期待地看向右侧<Picture 2>蓝衣男子，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S1) says: <d>[Chinese]那师兄还不快给我讲讲，如烟大帝后来怎么了？</d>
3.6～5.0秒：左侧<Picture 1>白衣女子闭上嘴，身体微微前倾做出认真倾听的姿态，右侧<Picture 2>蓝衣男子展颜一笑准备开口，保持双人同框构图。

overall_soundscape: 玉简入袖的布料摩擦声，山谷轻柔风声，平缓呼吸声。

non_diegetic_music: N/A`
      },
      {
        id: 4,
        title: '第四幕·同门约定收束 (45-60s)',
        subtitle: '双人同框不脱画 · 拱手约定缓拉远景',
        prompt: `【第10段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子同框而立，背景云海翻涌。
0.0～0.8秒：右侧<Picture 2>蓝衣男子看着左侧<Picture 1>白衣女子好奇的模样，微微一笑，抬起右手做了个保密的手势。
0.8～3.6秒：沉稳青年男子 (S2) says: <d>[Chinese]天机不可泄露，等你今晚筑基成功再借你看。</d>
3.6～5.0秒：右侧<Picture 2>蓝衣男子放下右手，左侧<Picture 1>白衣女子轻轻嘟嘴随后展颜微笑点头，画面停在两人同框相视的稳定瞬间。

overall_soundscape: 山间轻柔风声，衣袖摆动声，温和呼吸声。

non_diegetic_music: N/A

【第11段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台栏杆前。双人同框近景固定机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子并肩站在白玉栏杆前，两人始终完整留在画面中，斜上方仙境柔光均匀打亮两人面庞。
0.0～0.8秒：左侧<Picture 1>白衣女子微笑着向右侧<Picture 2>蓝衣男子拱手行了一个轻巧的同门礼，吸气准备说话。
0.8～3.6秒：清脆年轻女子 (S1) says: <d>[Chinese]一言为定，师兄可不许反悔。</d>
3.6～5.0秒：左侧<Picture 1>白衣女子礼毕放下双手，与右侧<Picture 2>蓝衣男子一同扶住身前的雕花白玉栏杆，画面停在两人同框远眺云海的瞬间。

overall_soundscape: 山风吹拂发丝与衣带的轻响，手掌轻触白玉栏杆的细微声响。

non_diegetic_music: N/A

【第12段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9古风奇幻画面，数字电影摄影机成像，色调柔和绚丽，涂山仙境白玉平台栏杆前。双人同框中景至远景平滑缓拉机位，左侧<Picture 1>白衣年轻女子与右侧<Picture 2>蓝衣青年男子并肩立于白玉栏杆前，两人全程保持在画面中央偏下位置同框不分离。
0.0～1.6秒：左侧<Picture 1>白衣女子与右侧<Picture 2>蓝衣男子并肩扶着白玉栏杆，微笑着望向云海深处的仙山楼阁，衣袂随微风轻轻飘动。
1.6～3.8秒：镜头从两人正前方极其缓慢平稳地向后拉远，完整保留两人同框身影的同时，展现涂山仙境层峦叠嶂的浮空仙岛与漫天霞光。
3.8～5.0秒：山间薄雾与金色霞光在两人周围缓缓流淌，画面平稳收束在<Picture 1>白衣女子与<Picture 2>蓝衣男子并肩同框的定格画面中。

overall_soundscape: 辽阔山谷的柔和风声，远处仙钟悠远回荡，衣袂随风轻摆声。

non_diegetic_music: N/A`
      }
    ]
  },
  cyberpunk: {
    name: '💻 极客实验室·AI觉醒 (AICG3D编译器·同空间60s)',
    skill_id: 'short-scene-director-lite',
    storyIdea: '深夜极客工作室内，年轻创始人独自坐在双屏工作台前完成最后一行回车，看着测试全绿通过，拿起桌上的马克杯喝了一口温热咖啡，起身走到落地窗前凝望城市夜色，立下新项目开启的誓言。',
    characterPrompt: '电影级真实主角半身定妆照，年轻科技创业者身穿深色连帽卫衣，坐在深夜极客实验室双屏显示器前，冷蓝屏幕光与暖色台灯交织映照面部轮廓，写实皮肤纹理，8k超清',
    acts: [
      {
        id: 1,
        title: '第一幕·敲击回车 (0-15s)',
        subtitle: '深夜极客工作台前 · 敲击键盘接等待测试',
        prompt: `【第1段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前同一空间。中近景平视机位，<Picture 1> 的年轻男性身穿深色卫衣坐在人体工学椅上，正前方双屏显示器泛着柔和的冷蓝色代码微光，右侧桌面一盏暖黄台灯平衡面部明暗。
0.0～1.8秒：男性身体微倾，双手在机械键盘上流畅敲击最后一行指令，眼神专注锁住主屏幕。
1.8～3.6秒：男性右手小指果断按下回车键，随即双手离开键盘自然平放在桌面腕托上。
3.6～5.0秒：男性上身向后轻靠椅背，屏住呼吸凝视屏幕中央滚动的进度条，画面停在他专注等待结果的姿态。

overall_soundscape: 机械键盘清脆的敲击声，最后一声有力的回车键声，机箱风扇低沉平稳的运转声。

non_diegetic_music: 极简轻柔的电子环境氛围音进入。

【第2段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。近景平视固定机位，<Picture 1> 的年轻男性靠在椅背上，双手平放在桌面腕托，屏幕冷光与台灯暖光延续上一镜头。
0.0～1.5秒：显示器屏幕亮起醒目的翠绿色通过界面，柔和的绿光瞬间映亮男性的眼镜与眉眼。
1.5～3.6秒：男性紧绷的嘴角缓缓扬起舒展的笑容，胸口起伏长长舒出一口气。
3.6～5.0秒：男性轻微吸气，目光对准主屏幕上方摄像头方向，画面停在他准备开口记录日志的瞬间。

overall_soundscape: 系统测试通过的清脆柔和提示音，男性如释重负的呼气声，室内安静底噪。

non_diegetic_music: 温暖的合成器垫音缓缓铺开。

【第3段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。近景平视固定机位，<Picture 1> 的年轻男性坐在工作台前，面部被翠绿屏幕光与暖黄台灯柔和照亮。
0.0～0.8秒：男性面带从容笑意，眼神清亮地注视前方，轻轻吸气。
0.8～3.6秒：男性 (S1) says: <d>[Chinese]连续跑了三十六个小时，全链路终于全绿通过了。</d>
3.6～5.0秒：男性闭上嘴巴，视线移向桌面右侧的黑色陶瓷咖啡杯，右手伸出，画面停在手指刚握住杯柄的瞬间。

overall_soundscape: 机箱微弱的运转声，衣袖擦过桌面的细微声响。

non_diegetic_music: 舒缓的钢琴单音轻轻点缀。`
      },
      {
        id: 2,
        title: '第二幕·举杯品咖 (15-30s)',
        subtitle: '深夜极客工作台前 · 端杯浅酌接放下杯子',
        prompt: `【第4段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。近景平视机位，<Picture 1> 的年轻男性坐在椅上，右手正握住桌面右侧的黑色陶瓷咖啡杯杯柄，光影色调保持一致。
0.0～1.8秒：男性右手平稳端起咖啡杯举至嘴边，杯口飘起一缕淡淡的热气。
1.8～3.6秒：男性轻轻抿了一口温热的咖啡，眉眼间流露出放松与满足的神态。
3.6～5.0秒：男性将咖啡杯从嘴边移开停在胸前高度，视线再次投向屏幕上的数据曲线，停在准备开口的瞬间。

overall_soundscape: 端起瓷杯离开木桌的轻响，轻柔的吞咽与呼吸声，室内静谧底噪。

non_diegetic_music: 温暖的钢琴与大提琴慢速流淌。

【第5段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。近景平视固定机位，<Picture 1> 的年轻男性右手端着咖啡杯停在胸前，目光注视屏幕，面部对焦清晰。
0.0～0.8秒：男性眼神笃定，嘴角微扬，胸口轻微吸气。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]把底层逻辑打磨干净，比堆砌任何花哨参数都管用。</d>
3.7～5.0秒：男性闭上嘴，右手将咖啡杯缓缓放回桌面杯垫上方，画面停在杯底刚触碰到木质杯垫的瞬间。

overall_soundscape: 温和的呼吸声，陶瓷杯底轻触木质杯垫的清脆声响。

non_diegetic_music: 钢琴旋律平稳推进。

【第6段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。中近景平视机位，<Picture 1> 的年轻男性右手刚把咖啡杯放在桌面杯垫上，暖黄台灯与冷蓝屏幕光保持稳定。
0.0～1.6秒：男性松开杯柄，左手轻轻合上桌面左侧的笔记本电脑顶盖。
1.6～3.6秒：男性双手撑住座椅扶手，身体重心前移准备从座椅上站起。
3.6～5.0秒：男性双腿发力向上站起一半，视线转向工作室右侧的落地窗方向，画面停在起身一半的衔接姿态。

overall_soundscape: 笔记本合盖的轻柔闭合声，人体工学椅滚轮轻微移动声，衣料摩擦声。

non_diegetic_music: 弦乐铺底轻微渐强。`
      },
      {
        id: 3,
        title: '第三幕·起身临窗 (30-45s)',
        subtitle: '深夜极客工作室内 · 起身迈步接驻足窗前',
        prompt: `【第7段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室内同一空间。中景平视跟拍机位，镜头随人物起身平滑右移，<Picture 1> 的年轻男性从工作台座椅完全站直身体，身旁就是通透的落地玻璃窗与城市夜景。
0.0～1.8秒：男性顺势完全站直，抬起双手做了一个自然的肩颈舒展动作。
1.8～3.6秒：男性放下双臂，向右侧迈出一步走到工作台旁的落地窗前驻足。
3.6～5.0秒：男性双手自然插进卫衣口袋，侧身望向窗外星星点点的城市灯火，画面停在他吸气准备感叹的瞬间。

overall_soundscape: 运动鞋踩在木地板上的两声轻稳脚步声，舒展肩膀的轻微呼吸声。

non_diegetic_music: 开阔的合成器与钢琴和弦展开。

【第8段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室落地窗前。近景平视机位，<Picture 1> 的年轻男性站在工作台旁的落地窗前，左侧是工作室桌面暖光，右侧是窗外深邃的城市霓虹夜色。
0.0～0.8秒：男性从窗外缓缓转过半张脸看向镜头方向，眼神深邃平静。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]凌晨四点的城市很安静，但这套系统才刚刚开始运转。</d>
3.7～5.0秒：男性闭上嘴巴，右手从卫衣口袋抽出并抬起手腕，画面停在他低头准备查看智能手表的瞬间。

overall_soundscape: 室内安静的空调微风声，手掌抽出衣袋的布料摩擦声。

non_diegetic_music: 温暖沉稳的大提琴旋律延续。

【第9段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室落地窗前。近景平视机位，<Picture 1> 的年轻男性站在窗前，右手手腕抬在胸前，光影冷暖层次统一。
0.0～1.6秒：男性低头看了一眼手腕上亮起微光的表盘，确认时间后轻轻点头。
1.6～3.6秒：男性放下右手，转身半步重新面向身旁泛着绿光的双屏工作台。
3.6～5.0秒：男性走到工作台侧边，右手伸向桌面上的无线鼠标，画面停在手掌刚覆上鼠标的瞬间。

overall_soundscape: 转身时轻微的脚步声，手掌触碰鼠标的细微轻响。

non_diegetic_music: 钢琴单音轻柔回旋。`
      },
      {
        id: 4,
        title: '第四幕·归档收官 (45-60s)',
        subtitle: '深夜极客工作台前 · 保存归档接关灯定格',
        prompt: `【第10段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。中近景平视机位，<Picture 1> 的年轻男性站在工作台侧旁，右手覆在无线鼠标上，双屏显示器与暖黄台灯映照着桌面。
0.0～1.8秒：男性食指轻轻点击鼠标左键，将测试报告与模型权重正式归档保存。
1.8～3.6秒：屏幕显示归档完成图标，男性直起腰身，目光自信地投向镜头前方。
3.6～5.0秒：男性胸口微扩吸气，面带微笑停在准备说出收官寄语的稳定姿态。

overall_soundscape: 鼠标左键清脆点击声，硬盘归档写入的轻微嗡鸣声。

non_diegetic_music: 温暖恢弘的弦乐和声轻轻托起。

【第11段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。近景平视固定机位，<Picture 1> 的年轻男性站在工作台旁正对镜头，冷暖光线均匀勾勒出他清晰自信的面容。
0.0～0.8秒：男性眼神明亮，嘴角扬起温暖坚定的笑容。
0.8～3.7秒：男性 (S1) says: <d>[Chinese]第一阶段圆满收口，天亮之后我们开启下一轮迭代。</d>
3.7～5.0秒：男性闭上嘴巴颔首致意，左手伸向桌面暖黄台灯的金属拉链开关，画面停在手指捏住开关链珠的瞬间。

overall_soundscape: 清晰的呼吸声，手指轻触金属链珠的细微碰撞声。

non_diegetic_music: 钢琴与弦乐进入温暖的收尾乐句。

【第12段｜5秒】
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 16:9电影感写实画面，深夜极客工作室双屏工作台前。中景缓拉机位，<Picture 1> 的年轻男性站在工作台旁，左手捏着台灯开关链珠。
0.0～1.6秒：男性手指轻轻一拉关掉桌面暖黄台灯，工作室内只留下双屏显示器清澈的冷蓝光晕与窗外晨曦微光。
1.6～3.8秒：男性收回左手，安静站在屏幕与落地窗之间，镜头从近景极其平滑地向后拉远。
3.8～5.0秒：天际线泛起第一抹淡淡的破晓蓝光，画面平稳收束在极客工作室静谧而充满希望的全景定格中。

overall_soundscape: 台灯拉链开关清脆的咔哒声，清晨微风拂过窗棂的轻柔声响。

non_diegetic_music: 钢琴尾音清澈回响后自然淡出。`
      }
    ]
  },
  desert: {
    name: '⚔️ 古风石阶·师兄师妹夜探 (无限视频模板V1原装延展60s)',
    skill_id: 'zh_dialogue',
    storyIdea: '古风冷色山谷高处石阶前，左侧<Picture 1>玄青劲装男师兄劝右侧<Picture 2>淡碧衣裙女师妹早回山门，师妹听见前方竹林石阶异响提醒先别动，两人全程保持双人同框按剑凝神并取出夜明珠照亮探查，发现是山间灵鹿碰响铜铃后相视一笑，巡山记毕并肩归门。',
    characterPrompt: '16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清',
    characterPrompts: [
      '16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清',
      '16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十岁出头清丽灵动的古风仙门女师妹，乌黑长发绾成灵动仙侠发髻并饰以碧玉发簪，身穿淡碧青色古风刺绣衣裙，斜上方清冷月光与暖色灯火柔和打亮精致五官，眼神机敏清澈，数字电影摄影机成像，8k超清'
    ],
    defaultReferenceJobs: ['e9c935272baa45b786c9e22bd82fe1fc', 'fc2be640fb62404b94ce66c798f7d13a'],
    acts: [
      {
        id: 1,
        title: '第一幕·石阶夜语 (0-15s)',
        subtitle: '无限视频模板V1第8节原装段1~3 · 双人同框锁定',
        prompt: `【第1段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷环境光与远处温暖山门灯火。开场中景平视缓推至双人同框近景，场景固定在高处青石台阶平台前，<Picture 1>玄青劲装青年男师兄站在画面左侧，<Picture 2>淡碧衣裙年轻女师妹站在画面右侧，两人并肩相对而立，面部五官均保持清晰锐利。
0.0～1.8秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹并肩站在高处石阶前，山间夜风轻轻吹拂两人衣袖与发丝。
1.8～3.6秒：镜头平滑推进至双人同框近景，左侧<Picture 1>玄青劲装男师兄缓慢转头看向右侧<Picture 2>淡碧衣裙女师妹，右手轻轻抬起停在半空。
3.6～5.0秒：镜头保持双人同框近景固定，画面停在左侧<Picture 1>男师兄转头吸气准备开口、右侧<Picture 2>女师妹安静聆听的稳定瞬间。

overall_soundscape: 高处山谷夜风声持续，远处山门灯火方向隐约风铃声，两人衣料轻微摩擦声。

non_diegetic_music: N/A

【第2段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定凝视机位，左侧<Picture 1>玄青劲装青年男师兄看向右侧<Picture 2>淡碧衣裙年轻女师妹，两人站在石阶原位不动，服装样貌与清冷月光保持一致，面部清晰对焦。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄轻轻吸了一口气，目光温和地落在右侧<Picture 2>淡碧衣裙女师妹脸上，女师妹闭口专注倾听。
0.8～3.5秒：沉稳青年男子 (S1) says: <d>[Chinese]师妹，夜深风急，我们先回山门吧。</d>
3.5～5.0秒：左侧<Picture 1>男师兄闭上嘴巴放下右手，右侧<Picture 2>女师妹神色微动似听见竹林方向传来声响，停在双人同框等待回应的瞬间。

overall_soundscape: 高处山风持续吹拂，衣料在夜风中轻轻摩擦，男子平缓呼吸声。

non_diegetic_music: N/A

【第3段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定凝视机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹站在石阶原位保持双人同框，两人面部全程清晰对焦。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹抬起敏锐的眼睛看向左侧<Picture 1>玄青劲装男师兄，右手在身侧微微收紧，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]前面石阶有声音，我们先别动。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴，与左侧<Picture 1>男师兄同时将目光警觉地投向画面右前方的石阶竹林暗处，停在双人同框戒备瞬间。

overall_soundscape: 高处山谷风声持续，前方竹林深处传来一声很轻的铜铃晃动声。

non_diegetic_music: N/A`
      },
      {
        id: 2,
        title: '第二幕·并肩探查 (15-30s)',
        subtitle: '无限视频模板V1原装段4 + 双人同框掌珠照阶',
        prompt: `【第4段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框中近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，严禁任何一人移出画面，光影色温保持统一。
0.0～1.5秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹同时屏住呼吸，目光紧锁右前方石阶竹林。
1.5～3.6秒：左侧<Picture 1>男师兄左脚向前稳踏半步，右手微微横在右侧<Picture 2>女师妹身前做出守护姿态，女师妹紧贴他身侧驻足。
3.6～5.0秒：左侧<Picture 1>男师兄左手稳稳按上腰间长剑剑柄，画面停在双人同框按剑凝神的衔接瞬间。

overall_soundscape: 靴底踏在青石台阶上的轻微回响，手掌按住剑柄的皮革摩擦声，夜风吹动竹叶声。

non_diegetic_music: N/A

【第5段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄左手按住剑柄，右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在他身侧，两人始终完整留在画面内。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄微微侧过半张脸示意右侧<Picture 2>淡碧衣裙女师妹安心，胸口轻微吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]跟在我身旁别离开，我们照亮看看。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手探入衣襟怀中准备取出一颗夜明珠，右侧<Picture 2>女师妹目光专注地看向他的右手，保持双人同框。

overall_soundscape: 山林夜风吹拂竹叶的沙沙声，衣襟布料摩擦声，沉稳呼吸声。

non_diegetic_music: N/A

【第6段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷环境光叠加夜明珠温润白光。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，两人面部清晰锐利。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄右手从怀中取出一颗泛着温润白光的夜明珠，平托在两人胸前中央照亮前方青石台阶。
1.6～3.6秒：伴随竹林方向传来一声清脆的灵鹿轻鸣，左侧<Picture 1>男师兄与右侧<Picture 2>女师妹紧绷的眉宇同时舒展开来，男师兄左手松开腰间剑柄。
3.6～5.0秒：左侧<Picture 1>男师兄嘴角浮现温和笑意转头看向右侧<Picture 2>女师妹，停在吸气准备开口解释的双人同框瞬间。

overall_soundscape: 竹叶轻微晃动声，林间一声清脆柔和的灵鹿轻鸣，手掌松开剑柄的轻响。

non_diegetic_music: N/A`
      },
      {
        id: 3,
        title: '第三幕·虚惊一笑 (30-45s)',
        subtitle: '双人同框固定机位 · 灵鹿夜行解除戒备',
        prompt: `【第7段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷月光与掌心夜明珠柔光交融。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手托着发光夜明珠，右侧<Picture 2>淡碧衣裙年轻女师妹站在身旁，两人全程保持在画面左右两侧。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄面带温和笑意看向右侧<Picture 2>淡碧衣裙女师妹，轻轻吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]原来是山里的灵鹿碰响了竹林铜铃。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴微笑注视她，右侧<Picture 2>女师妹长舒一口气展露浅笑，画面停在两人相视放松的双人同框瞬间。

overall_soundscape: 山间轻柔夜风声，远处灵鹿轻快跃入竹林的细微蹄声，轻柔呼吸声。

non_diegetic_music: N/A

【第8段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷月光与夜明珠柔光。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹站在石阶原位，夜明珠微光柔和映亮两人面庞。
0.0～0.7秒：右侧<Picture 2>淡碧衣裙女师妹紧握的右手自然垂落，脸上露出几分不好意思的浅笑看向左侧<Picture 1>男师兄，轻微吸气。
0.7～3.5秒：清脆年轻女子 (S2) says: <d>[Chinese]害师兄白白按剑紧张一场，让你见笑了。</d>
3.5～5.0秒：右侧<Picture 2>女师妹闭上嘴巴微微颔首浅笑，左侧<Picture 1>男师兄温和摇了摇头准备开口宽慰，保持双人同框构图。

overall_soundscape: 女子轻柔的呼气声，衣袖自然垂落的布料摩擦声，山谷夜风声。

non_diegetic_music: N/A

【第9段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹面对面站在青石台阶旁，两人面部光影清晰自然。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄眼神温和笃定地注视右侧<Picture 2>淡碧衣裙女师妹，轻微吸气。
0.8～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]夜探山道小心为上，师妹这份警觉正是好事。</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手将夜明珠妥帖收回怀中，右侧<Picture 2>女师妹欣然点头微笑，停在双人同框的衔接姿态。

overall_soundscape: 夜明珠收入怀中的衣料摩擦声，山间清风声，平和的呼吸声。

non_diegetic_music: N/A`
      },
      {
        id: 4,
        title: '第四幕·拾级归山 (45-60s)',
        subtitle: '双人同框不脱画 · 巡山记毕缓拉远景',
        prompt: `【第10段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台，背景远处山门灯火温暖闪烁。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶栏杆旁。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹拍了拍腰间收纳袋，眼神清亮地看向左侧<Picture 1>玄青劲装男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]巡山记录已经记好了，师兄我们回山门复命吧。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴面带微笑，左侧<Picture 1>男师兄颔首赞许并望向山门灯火方向，画面停在两人同框相视的稳定瞬间。

overall_soundscape: 手掌轻拍收纳袋的细微声响，山谷轻柔夜风声，两人温和呼吸声。

non_diegetic_music: N/A

【第11段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶前，两人始终完整留在画面中。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄从山门方向收回视线看向右侧<Picture 2>淡碧衣裙女师妹，神情从容温和，轻微吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]好，趁着夜雾还没起，我们这就上去。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手做出请行的温和手势，右侧<Picture 2>女师妹微笑拱手回礼，画面停在两人同框并肩的瞬间。

overall_soundscape: 山顶传来的悠远夜钟声，轻柔山风吹拂发丝与衣带的声响。

non_diegetic_music: N/A

【第12段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框中景至远景平滑缓拉机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩立于高处青石台阶之上，两人全程保持在画面中央同框不分离。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹并肩站在高处石阶平台，微笑着望向远处灯火通明的古风山门，衣袂随清风轻轻飘动。
1.6～3.8秒：镜头从两人正前方极其缓慢平稳地向后拉远，完整保留两人同框身影的同时，展现高处青石台阶、幽深竹林山谷与山巅璀璨灯火。
3.8～5.0秒：山谷薄雾与清冷月光在两人周围缓缓流淌，画面平稳收束在<Picture 1>男师兄与<Picture 2>女师妹并肩同框的定格画卷中。

overall_soundscape: 辽阔山谷的柔和夜风声，远处古寺山门钟声悠远回荡，衣袂随风轻摆声。

non_diegetic_music: N/A`
      }
    ]
  },
  desert_sequel: {
    name: '🦌 古风石阶·续集：灵鹿衔玉示警 (60s双采精修版)',
    characterPrompt: '16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清',
    characterPrompts: [
      '16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十五岁俊朗沉稳的古风仙门男师兄，束发佩戴古铜发冠，身穿深玄青色修身古风剑客劲装长袍，腰悬长剑，斜上方清冷月光与远处暖色山门灯火均匀打亮立体五官，眼神温和锐利，数字电影摄影机成像，8k超清',
      '16:9古风武侠电影定妆照，冷色调山谷竹林石阶背景，二十岁出头清丽灵动的古风仙门女师妹，乌黑长发绾成灵动仙侠发髻并饰以碧玉发簪，身穿淡碧青色古风刺绣衣裙，斜上方清冷月光与暖色灯火柔和打亮精致五官，眼神机敏清澈，数字电影摄影机成像，8k超清'
    ],
    acts: [
      {
        index: 1,
        title: '第一幕 · 鹿鸣落玉与掌心端详（0-15s｜山门石阶·双人同框）',
        prompt: `【第1段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷夜色与山门前温暖灯笼光交融。开场中景平视缓推至双人同框近景，场景固定在山门外高处青石台阶平台前，<Picture 1>玄青劲装青年男师兄站在画面左侧，<Picture 2>淡碧衣裙年轻女师妹站在画面右侧，两人并肩而立，面部五官清晰锐利。
0.0～1.8秒：左侧<Picture 1>玄青劲装男师兄与右侧<Picture 2>淡碧衣裙女师妹刚踏上山门前的青石平台，竹林暗处忽然传来一声清越的灵鹿轻鸣与玉石落地清脆声。
1.8～3.6秒：镜头平滑推进至双人同框近景，左侧<Picture 1>男师兄与右侧<Picture 2>女师妹同时停下脚步，目光讶异地低头看向两人前方石阶地面。
3.6～5.0秒：右侧<Picture 2>女师妹抬起头望向左侧<Picture 1>男师兄，微张嘴吸气准备开口，停在双人同框警觉对视的稳定瞬间。

overall_soundscape: 竹林方向传来一声清脆灵鹿轻鸣，紧接着一声温润玉器落在青石板上的清脆轻响，山谷夜风声。

non_diegetic_music: N/A

【第2段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台与温暖山门灯火。双人同框近景固定凝视机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，两人服装样貌与光影保持一致，面部清晰对焦。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹右手轻轻指向前方石阶地面，眼神惊讶地看向左侧<Picture 1>玄青劲装男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]师兄你看，方才那只灵鹿在石阶上留下了一块玉佩。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴收回右手，左侧<Picture 1>男师兄神色凝重地颔首，停在双人同框准备拾取玉佩的衔接瞬间。

overall_soundscape: 远处灵鹿轻快跃入竹林的细碎蹄声，高处山风吹拂衣袂声，女子轻柔呼吸声。

non_diegetic_music: N/A

【第3段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹站在石阶原位保持双人同框，两人面部全程清晰对焦。
0.0～1.0秒：左侧<Picture 1>玄青劲装男师兄右手向上抬起，掌心托着一枚泛着温润青白色微光的古朴雕花玉佩停在两人胸前中央，轻微吸气。
1.0～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]灵鹿通灵去而复返，莫非是特意来向我们示警？</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，与右侧<Picture 2>女师妹同时低头凝视他右手掌心中的发光古玉，停在双人同框端详玉佩的瞬间。

overall_soundscape: 手指拾起温润玉佩的轻微摩擦声，山门方向檐角风铃轻响，山谷夜风声。

non_diegetic_music: N/A`
      },
      {
        index: 2,
        title: '第二幕 · 玄鸦暗纹与名册核查（15-30s｜古玉特写接力·双人同框）',
        prompt: `【第4段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷月光叠加掌心古玉温润青光。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手平托发光古玉，右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在身侧，严禁任何一人移出画面。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄右手手指将掌心泛着青光的古玉轻轻翻转，借着月光与灯火细看玉佩背面的刻印。
1.6～3.6秒：随着古玉背面图腾显露，左侧<Picture 1>男师兄眉头微微蹙起，右侧<Picture 2>女师妹也凑近半寸专注端详，两人神情渐趋严肃。
3.6～5.0秒：左侧<Picture 1>男师兄抬起目光看向右侧<Picture 2>女师妹，停在吸气准备开口说出发现的双人同框瞬间。

overall_soundscape: 指尖翻转玉佩的细腻摩擦声，夜风拂过竹林的沙沙声，两人屏息凝神的轻微呼吸声。

non_diegetic_music: N/A

【第5段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手托着古玉，右侧<Picture 2>淡碧衣裙年轻女师妹站在他身旁，两人始终完整留在画面内。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄眼神深邃笃定地注视右侧<Picture 2>淡碧衣裙女师妹，胸口轻微吸气。
0.8～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]玉佩背面刻着玄鸦暗纹，绝非我们本门弟子的信物。</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴稳托古玉，右侧<Picture 2>女师妹神色一凛，双手从腰间取出宗门名册卷轴，保持双人同框。

overall_soundscape: 山谷夜风持续吹拂，女子从腰间取出纸质卷轴的轻微摩擦声，沉稳呼吸声。

non_diegetic_music: N/A

【第6段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷月光与温暖山门灯火。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄右手托玉，右侧<Picture 2>淡碧衣裙年轻女师妹双手展开一卷古风纸质名册，两人面部清晰锐利。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹快速扫视手中展开的宗门名册卷轴，随后抬眼看向左侧<Picture 1>男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]我查过访客名册，今夜并未登记有外门来客入山。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴，目光再次落向左侧<Picture 1>男师兄手中的古玉，画面停在两人同框思索的瞬间。

overall_soundscape: 纸质名册卷轴展开与翻动的沙沙声，高处山谷清冷风声，平缓呼吸声。

non_diegetic_music: N/A`
      },
      {
        index: 3,
        title: '第三幕 · 尚存余温与按剑戒备（30-45s｜双人同框固定机位）',
        prompt: `【第7段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶原位，两人全程保持在画面左右两侧。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹指尖轻轻触碰了一下左侧<Picture 1>男师兄掌心的古玉边缘，眼神敏锐地抬起看向他，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]玉佩尚有余温，说明那人刚刚潜入山门外围不久。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴收回手指，左侧<Picture 1>男师兄左手迅速按上腰间剑柄，停在双人同框警觉戒备的瞬间。

overall_soundscape: 指尖轻触玉石的细微声响，手掌按住长剑剑柄的皮革摩擦声，山林夜风声。

non_diegetic_music: N/A

【第8段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹面对面站在青石台阶旁，两人面部光影清晰自然。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄眼神凌厉而沉着地看向右侧<Picture 2>淡碧衣裙女师妹，轻微吸气。
0.8～3.6秒：沉稳青年男子 (S1) says: <d>[Chinese]看来方才铜铃作响，正是有人暗中惊动了林中灵鹿。</d>
3.6～5.0秒：左侧<Picture 1>男师兄闭上嘴巴，右手将古玉稳妥收入衣襟怀中，右侧<Picture 2>女师妹将手中名册卷轴合拢，保持双人同框构图。

overall_soundscape: 玉佩收入怀中的衣料摩擦声，纸质名册卷轴合拢声，山谷夜风吹动竹叶声。

non_diegetic_music: N/A

【第9段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台，背景山门灯火温暖明亮。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶前，两人始终完整留在画面中。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹将名册卷轴收回腰间，神情郑重地看向左侧<Picture 1>玄青劲装男师兄，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]事关护山大阵安危，我们必须立刻带玉佩禀报掌门。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴眼神坚定地注视师兄，左侧<Picture 1>男师兄郑重颔首准备下令，停在双人同框的衔接姿态。

overall_soundscape: 卷轴收入腰间收纳袋的轻响，山间清风声，两人沉稳的呼吸声。

non_diegetic_music: N/A`
      },
      {
        index: 4,
        title: '第四幕 · 携玉直赴主殿与远景收束（45-60s｜双人同框缓拉远景）',
        prompt: `【第10段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色山谷高处石阶平台与温暖山门灯火。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶栏杆旁，面部清晰对焦。
0.0～0.8秒：左侧<Picture 1>玄青劲装男师兄目光坚定地注视右侧<Picture 2>淡碧衣裙女师妹，轻微吸气。
0.8～3.7秒：沉稳青年男子 (S1) says: <d>[Chinese]好，你随我直赴主殿呈交古玉，沿途留心暗处动静。</d>
3.7～5.0秒：左侧<Picture 1>男师兄闭上嘴巴左手按剑蓄势，右侧<Picture 2>女师妹双手抱拳准备领命，画面停在两人同框相视的稳定瞬间。

overall_soundscape: 衣袖抬起的布料摩擦声，山谷轻柔夜风声，远处山门传来的低沉钟鸣声。

non_diegetic_music: N/A

【第11段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台。双人同框近景固定机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩站在石阶前，两人始终完整留在画面中。
0.0～0.8秒：右侧<Picture 2>淡碧衣裙女师妹向左侧<Picture 1>玄青劲装男师兄行了一个干脆利落的古风抱拳礼，眼神清亮笃定，轻微吸气。
0.8～3.6秒：清脆年轻女子 (S2) says: <d>[Chinese]明白，有师兄在身旁，今晚定叫来人无所遁形。</d>
3.6～5.0秒：右侧<Picture 2>女师妹闭上嘴巴放下双手，左侧<Picture 1>男师兄露出一丝赞许微笑并肩转向山门方向，停在两人同框并肩的瞬间。

overall_soundscape: 抱拳行礼时衣袖清脆的摩擦声，山顶传来的悠远夜钟声，轻柔山风声。

non_diegetic_music: N/A

【第12段｜5秒】
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 5.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] 16:9电影感古风画面，数字电影摄影机成像，冷色调山谷高处石阶平台与辉煌山门灯火。双人同框中景至远景平滑缓拉机位，左侧<Picture 1>玄青劲装青年男师兄与右侧<Picture 2>淡碧衣裙年轻女师妹并肩立于山门青石台阶之上，两人全程保持在画面中央同框不分离。
0.0～1.6秒：左侧<Picture 1>玄青劲装男师兄左手按剑，与右侧<Picture 2>淡碧衣裙女师妹并肩站在山门石阶平台，神情坚定地望向灯火辉煌的仙门主殿。
1.6～3.8秒：镜头从两人正前方极其缓慢平稳地向后拉远，完整保留两人同框身影的同时，展现高耸古风山门、幽深竹林石阶与漫天清冷月色。
3.8～5.0秒：山谷云雾与暖色灯火在两人周围交织流淌，画面平稳收束在<Picture 1>男师兄与<Picture 2>女师妹并肩守卫山门的史诗画卷中。

overall_soundscape: 辽阔山谷的柔和夜风声，古寺山门浑厚钟声悠远回荡，衣袂随风轻摆声。

non_diegetic_music: N/A`
      }
    ]
  }
};

const SEQUENCE_TEMPLATES = {
  detective: DRAMA_PRESETS.cyberpunk.acts.map(a => a.prompt).join('\n\n'),
  fight: DRAMA_PRESETS.desert.acts.map(a => a.prompt).join('\n\n'),
  seq_30s: DRAMA_PRESETS.tushan.acts.slice(0, 2).map(a => a.prompt).join('\n\n'),
  seq_60s: DRAMA_PRESETS.island.acts.map(a => a.prompt).join('\n\n')
};

const DRAMA_STORAGE = 'studio-generation-lab-test-v4-aicg3d-continuous';
let currentDramaPreset = 'island';
let actsState = JSON.parse(JSON.stringify(DRAMA_PRESETS.island.acts)).map(a => ({
  ...a, job: null, status: 'idle', elapsed: 0
}));

function getSelectedSeqSeconds(){
  const val = Number($('#lab-seq-seg-seconds')?.value);
  return (val && val >= 1 && val <= 30) ? val : 5;
}

function getSelectedFramesForSeconds(sec){
  if(sec >= 4.4 && sec <= 4.9) return 119;
  if(sec >= 4.91 && sec <= 5.2) return 124;
  if(sec >= 6.8 && sec <= 8.2) return 185;
  if(sec >= 9.8 && sec <= 10.2) return 243;
  if(sec >= 14.8 && sec <= 15.2) return 362;
  return Math.max(39, Math.round(sec * 24));
}

function getSelectedRefImageSize(){
  return $('#lab-ref-image-size')?.value || '1k';
}

function splitActIntoSubPrompts(actPrompt, targetCount){
  const raw = String(actPrompt || '').trim();
  if(!raw || targetCount <= 1){
    return [raw.replace(/【(?:第\s*\d+\s*段|分镜\s*\d+)(?:[｜\|]\s*[\d.]+\s*秒?)?】\s*/gi, '').trim()];
  }
  const tagRegex = /【(?:第\s*\d+\s*段|分镜\s*\d+)(?:[｜\|]\s*[\d.]+\s*秒?)?】/gi;
  const tagMatches = [...raw.matchAll(tagRegex)];
  let parts = [];
  if(tagMatches.length >= 2){
    for(let i = 0; i < tagMatches.length; i++){
      const start = tagMatches[i].index + tagMatches[i][0].length;
      const end = (i + 1 < tagMatches.length) ? tagMatches[i + 1].index : raw.length;
      const body = raw.slice(start, end).trim();
      if(body) parts.push(body);
    }
  } else {
    const beatLines = raw.split('\n');
    const headerLines = [];
    const beats = [];
    for(const line of beatLines){
      if(/^\s*Beat\s*\d+/i.test(line)){
        beats.push(line.trim());
      } else if(!beats.length){
        headerLines.push(line.trim());
      } else {
        beats[beats.length - 1] += '\n' + line.trim();
      }
    }
    const header = headerLines.filter(Boolean).join('\n');
    if(beats.length >= 2){
      parts = beats.map(b => (header ? header + '\n' + b : b));
    } else {
      parts = [raw];
    }
  }
  while(parts.length < targetCount){
    parts.push(parts[parts.length - 1] || raw);
  }
  if(parts.length > targetCount){
    if(targetCount === 2 && parts.length === 3){
      parts = [parts[0] + '\n\n' + parts[1], parts[2]];
    } else {
      parts = parts.slice(0, targetCount);
    }
  }
  return parts;
}

function buildSixtySecondSegments(validActs, segSec){
  const subCountPerAct = segSec <= 5.5 ? 3 : (segSec <= 10.5 ? 2 : 1);
  const rawPrompts = [];
  for(const act of validActs){
    const subPrompts = splitActIntoSubPrompts(act.prompt, subCountPerAct);
    for(const sp of subPrompts){
      if(sp) rawPrompts.push(sp);
    }
  }
  return rawPrompts.map((rawP, idx) => {
    // 强制将 non_diegetic_music 设为 N/A，防止每 5 秒独立生成不同配乐导致接缝硬切
    let p = String(rawP || '').replace(/non_diegetic_music:\s*[^\n]+/gi, 'non_diegetic_music: N/A');
    // 防止模糊的“主角 (S1)”或“人物 (S1)”导致男女串音
    const isFemaleLead = /白衣.*女子|女弟子|师妹|女仙|少女/.test(p);
    p = p.replace(/(?:主角|人物)\s*\((S\d+)\)\s*says:/g, (_, sTag) => {
      if(sTag === 'S1') return `${isFemaleLead ? '清脆年轻女子' : '沉稳年轻男性'} (${sTag}) says:`;
      return `沉稳青年男子 (${sTag}) says:`;
    });
    let sec = segSec;
    if(subCountPerAct === 3){
      // keep_full=True 下：前2段用 5.0s (124+136=260帧)，后10段用 4.8s (10×119=1190帧)，12段合计 1450帧 = 60.42秒
      sec = idx < 2 ? 5.0 : 4.8;
    } else if(subCountPerAct === 2){
      // keep_full=True 下：8段用 7.0s (175+7×187=1484帧 = 61.83秒)
      sec = 7.0;
    } else {
      // keep_full=True 下：4段用 15.0s (362+3×374=1484帧 = 61.83秒)
      sec = 15.0;
    }
    const fc = getSelectedFramesForSeconds(sec);
    return {
      index: idx + 1,
      seconds: sec,
      frameCount: fc,
      body: p,
      prompt: p
    };
  });
}

// ----------------------------------------------------
// 基础工具函数
// ----------------------------------------------------
function readLabDraft(){
  try{
    return JSON.parse(localStorage.getItem(labStorage)||'null');
  }catch{
    return null;
  }
}

function parseSequencePrompt(text){
  if(!text) return [];
  const regex = /(?:【(?:第\s*(\d+)\s*段|分镜\s*(\d+))(?:[｜\|]\s*([\d.]+)\s*秒?)?】|第\s*(\d+)\s*段)/gi;
  const matches = [...text.matchAll(regex)];
  if(!matches.length) return [];
  const segments = [];
  for(let i=0; i<matches.length; i++){
    const m = matches[i];
    const num = m[1] || m[2] || m[4] || (i+1);
    const sec = parseFloat(m[3]) || getSelectedSeqSeconds();
    const startIdx = m.index + m[0].length;
    const endIdx = (i+1 < matches.length) ? matches[i+1].index : text.length;
    const body = text.slice(startIdx, endIdx).trim();
    const fc = getSelectedFramesForSeconds(sec);
    segments.push({
      index: parseInt(num, 10) || (i+1),
      seconds: sec,
      frameCount: fc,
      body: body,
      prompt: body
    });
  }
  return segments;
}

function ensureR2VMode(){
  const select = $('#lab-workflow');
  if(!select) return;
  if(select.value === 'r2v'){
    return;
  }
  const savedImages = (typeof videoInputs !== 'undefined' && Array.isArray(videoInputs.images))
    ? videoInputs.images.filter(i => !i.removed && !i.error)
    : [];
  const vp = (typeof videoParameters === 'function') ? videoParameters() : {};
  if([...select.options].some(o => o.value === 'r2v')){
    select.value = 'r2v';
  }
  configureLab({
    ...currentLabValues(),
    references: vp.references,
    reference_media: vp.reference_media,
    workflow: 'r2v',
    video_checkpoint: 'hybrid',
    video_accelerator: 'none',
    video_ref_image_size: getSelectedRefImageSize()
  });
  if(savedImages.length && typeof videoInputs !== 'undefined' && (!videoInputs.images || !videoInputs.images.length)){
    videoInputs.images = savedImages.map(item => ({ ...item, removed: false, epoch: typeof videoInputEpoch !== 'undefined' ? videoInputEpoch : item.epoch }));
    renderVideoRefs();
  }
  rememberLab();
}

async function safeAttachVideoGalleryImage(slot, jobId){
  if(slot === 'images'){
    ensureR2VMode();
  } else if(slot === 'first'){
    const select = $('#lab-workflow');
    if(select && select.value !== 'i2v'){
      select.value = 'i2v';
      select.dispatchEvent(new Event('change'));
      configureLab({ ...currentLabValues(), workflow: 'i2v' });
    }
  }
  return await attachVideoGalleryImage(slot, jobId);
}

function labWorkflow(){
  return $('#lab-workflow')?.value || 't2v';
}

function labParameters(){
  const modelId = $('#lab-model')?.value || 'h3';
  const model = lab.models.find(m => m.id === modelId) || {};
  let frames = 56;
  try { frames = selectedMediaFrames('lab', model); } catch { frames = Number($('#lab-frames')?.value) || 56; }
  let size = '768x448';
  try { size = selectedMediaSize('media', labWorkflow()); } catch { size = $('#media-size')?.value || '768x448'; }
  let loraParams = {};
  try { loraParams = selectedLoraParameters('media', modelId); } catch {}

  const segSec = getSelectedSeqSeconds();
  const segFc = getSelectedFramesForSeconds(segSec);
  const refImgSize = getSelectedRefImageSize();

  let promptText = ($('#lab-prompt')?.value || '').trim();
  let seqSegments = parseSequencePrompt(promptText);
  if(seqSegments.length <= 1 && typeof actsState !== 'undefined' && Array.isArray(actsState)){
    const validActs = actsState.filter(a => (a.prompt || '').trim());
    if(validActs.length > 1){
      seqSegments = buildSixtySecondSegments(validActs, segSec);
      promptText = seqSegments.map(s => `【第${s.index}段｜${s.seconds}秒】\n${s.prompt}`).join('\n\n');
    }
  }
  const isSequence = seqSegments.length > 1;

  if(isSequence){
    frames = seqSegments.reduce((sum, s) => sum + (s.frameCount || 124), 0);
  }

  const vParams = videoParameters();
  const hasRefs = (typeof videoInputs !== 'undefined' && (videoInputs.images || []).filter(i => !i.error && !i.removed).length > 0);
  if(vParams.workflow === 'r2v' && !hasRefs){
    vParams.workflow = 't2v';
  }
  if(size === '768x432' || size === '432x768'){
    size = promptText.toLowerCase().includes('vertical') ? '448x768' : '768x448';
  }
  const sizeParts = String(size).split('x').map(Number);
  if(sizeParts.length !== 2 || sizeParts.some(n => !n || n % 32 !== 0)){
    size = promptText.toLowerCase().includes('vertical') ? '448x768' : '768x448';
  }

  return {
    model: modelId,
    prompt: promptText,
    size: size,
    seed: $('#lab-seed')?.value || '',
    ...loraParams,
    frames: frames,
    ...vParams,
    video_ref_image_size: refImgSize,
    ...(isSequence ? { is_sequence: true, segments: seqSegments } : {})
  };
}

function currentLabValues(){
  const modelId = $('#lab-model')?.value || 'h3';
  const model = lab.models.find(m => m.id === modelId) || {};
  const values = { seed: $('#lab-seed')?.value };
  try{ values.size = selectedMediaSize('media', labWorkflow()); }catch{}
  try{ Object.assign(values, selectedLoraParameters('media', modelId)); }catch{}
  for(const [id, key] of [['lab-video-sampling','video_sampling'],['lab-video-refine-steps','video_refine_steps'],['lab-video-refine-denoise','video_refine_denoise'],['lab-video-upscale','video_upscale']]){
    if($('#'+id)) values[key] = id==='lab-video-sampling' ? $('#'+id).value : Number($('#'+id).value);
  }
  try{ values.frames = selectedMediaFrames('lab', model); }catch{}
  for(const [id, key] of [['lab-workflow','workflow'],['lab-checkpoint','video_checkpoint'],['lab-accelerator','video_accelerator'],['lab-ref-image-size','video_ref_image_size'],['lab-seq-seg-seconds','seq_seg_seconds']]){
    if($('#'+id)?.value) values[key] = $('#'+id).value;
  }
  return values;
}

function rememberLab(){
  try{
    const params = labParameters();
    params.seq_seg_seconds = getSelectedSeqSeconds();
    localStorage.setItem(labStorage, JSON.stringify(params));
  }catch(e){
    console.warn('rememberLab failed:', e);
  }
}

function configureLab(settings={}){
  if(!settings.video_checkpoint) settings.video_checkpoint = 'hybrid';
  if(!settings.video_accelerator) settings.video_accelerator = 'none';
  configureVideo(settings);
  if($('#lab-ref-image-size') && settings.video_ref_image_size){
    $('#lab-ref-image-size').value = settings.video_ref_image_size;
  }
  if($('#lab-seq-seg-seconds') && settings.seq_seg_seconds){
    $('#lab-seq-seg-seconds').value = String(settings.seq_seg_seconds);
  }
  const model = lab.models.find(m => m.id === $('#lab-model').value);
  const workflow = labWorkflow();
  const sizeModel = workflow !== 't2v' ? filteredSizeModel(model) : model;
  const previousSize = resolveMediaSize('media', $('#media-size').value);
  const requestedSize = resolveMediaSize('media', settings.size || previousSize);
  const keepSize = !!sizeModel && ((sizeModel.sizes||[]).includes(requestedSize) || customSizeAllowed(sizeModel, requestedSize));
  renderMediaSize('media', sizeModel, keepSize ? requestedSize : (sizeModel?.sizes||[])[0] || requestedSize);
  const chosenSize = $('#media-size').value;
  const sizeNotice = (requestedSize && !keepSize && chosenSize !== requestedSize)
    ? (workflow !== 't2v' ? mediaWorkflowSizeNotice(requestedSize, workflow, chosenSize)
      : `（${requestedSize} 不适用于当前选择，已改为 ${chosenSize}）`) : '';
  $('#media-size-note').textContent = sizeNotice;
  const currentFrames = $('#lab-frames').value;
  renderMediaFrames('lab', model, settings.frames ?? (currentFrames === '' ? null : Number(currentFrames)));
  renderLoraFields('media', lab.models, model?.id, settings);
  if($('#lab-steps')) $('#lab-steps').value = settings.steps ?? model?.steps;
  if($('#lab-cfg')) $('#lab-cfg').value = settings.cfg ?? model?.cfg ?? 1;
  $('#lab-duration-label').hidden = true;
  if($('#lab-frames-note')) $('#lab-frames-note').hidden = true;
  $('#lab-style-label').hidden = true;
  $('#lab-sampling').hidden = true;
  for(const id of ['lab-steps', 'lab-cfg']){
    if($('#'+id)) $('#'+id).disabled = true;
  }
  updateSequenceUI();
}

function fillLab(job){
  const base = job.image_skill?.user_prompt || job.input_prompt || job.prompt;
  $('#lab-model').value = job.model;
  configureLab(job.settings);
  $('#lab-prompt').value = base;
  $('#lab-seed').value = job.settings.seed;
  rememberLab();
  updateSequenceUI();
  $('#lab-prompt').focus();
  toast('已沿用历史任务描述、种子与配置！');
}

function jobSummary(job){
  const s = job.settings || {};
  return `${s.size || ''} · 种子 ${s.seed ?? ''}${s.seconds ? ' · ' + s.seconds + ' 秒' + (s.workflow_name ? ' · ' + s.workflow_name : '') : ' · ' + (s.steps||'') + ' 步'}${job.kind==='video' ? (s.video_sampling==='dual' ? ' · 双采' : ' · 单采') : ''}`;
}

function jobExport(job){
  return {
    id: job.id,
    status: job.status,
    model: job.model,
    model_name: job.model_name,
    created_at: job.created_at,
    prompt: job.image_skill?.user_prompt || job.input_prompt || job.prompt,
    effective_prompt: job.prompt,
    ...job.settings,
    error: job.error || null
  };
}

const LAB_MANAGE_STATUSES = ['done', 'error', 'cancelled'];
function labManageable(job){
  return LAB_MANAGE_STATUSES.includes(job && job.status);
}

function renderLabJob(job){
  lab.selected = job;
  $('.lab-result').dataset.status = job.status;
  $('#lab-result-model').textContent = job.model_name;
  $('#lab-progress').hidden = false;
  renderMediaProgress($('#lab-progress'), job);
  attachMediaProgressHost($('#lab-progress'), { onMediaAction: fresh => { if(fresh.id === job.id) refreshLab(); } });

  const isTerminal = ['done', 'error', 'cancelled'].includes(job.status);
  const cancelBtn = $('#lab-cancel-result');
  if(cancelBtn){
    cancelBtn.hidden = isTerminal;
    cancelBtn.onclick = async () => {
      cancelBtn.disabled = true;
      cancelBtn.textContent = '⏹️ 正在停止…';
      try{
        await api('/api/generation/job/' + job.id + '/cancel', {});
        toast('已发送停止指令，4090 算力已释放');
        await refreshLab();
      }catch(err){
        toast('停止失败: ' + err.message);
      }finally{
        cancelBtn.disabled = false;
        cancelBtn.textContent = '⏹️ 停止生成';
      }
    };
  }

  const key = [job.id, job.status, job.asset_url].join(':');
  if(lab.previewKey !== key){
    lab.previewKey = key;
    const view = $('#lab-preview');
    view.querySelector('video')?.pause();
    view.replaceChildren();
    if(job.status === 'done'){
      const media = document.createElement(job.kind === 'image' ? 'img' : 'video');
      media.src = job.asset_url;
      if(job.kind === 'image'){
        media.alt = '生成结果';
      }else{
        media.controls = true;
        media.playsInline = true;
        media.preload = 'metadata';
        if(job.thumbnail_url) media.poster = job.thumbnail_url;
      }
      view.append(media);
    }else if(job.status === 'cancelled'){
      const box = document.createElement('div');
      box.className = 'lab-empty';
      const h = document.createElement('h3'), p = document.createElement('p');
      h.textContent = '任务已停止';
      p.textContent = '该任务已手动中止，4090 算力已释放。可随时重新发起生成或定妆。';
      box.append(h, p);
      view.append(box);
    }else if(job.status === 'error'){
      const box = document.createElement('div');
      box.className = 'lab-empty';
      const h = document.createElement('h3'), p = document.createElement('p');
      h.textContent = '这次生成未完成';
      p.textContent = job.error || '生成失败，请检查参数或重试。';
      box.append(h, p);
      view.append(box);
    }else{
      const box = document.createElement('div');
      box.className = 'lab-empty';
      const h = document.createElement('h3'), p = document.createElement('p');
      h.textContent = '视频正在渲染中…';
      p.textContent = '正在调用 4090 MiniMax H3 渲染中，稍候可在此预览。';
      box.append(h, p);
      view.append(box);
    }
  }
  $('#lab-result-actions').hidden = false;
  $('#lab-save').hidden = job.status !== 'done';
  $('#lab-download').hidden = job.status !== 'done';
  $('#lab-job-details').hidden = false;
  $('#lab-job-json').textContent = JSON.stringify(jobExport(job), null, 2);
  $('#lab-download').href = job.asset_url || '#';
  $('#lab-download').download = '测试生成-' + job.id.slice(0, 8) + (job.kind === 'image' ? '.png' : '.mp4');
  $('#lab-delete-result').disabled = !labManageable(job);
  $('#lab-delete-result').textContent = job.deleted_at ? '恢复记录' : '删除记录';
  $('#lab-save').textContent = job.saved?.new ? '打开已保存作品' : '保存为作品';
}

function renderLabHistory(){
  const query = $('#lab-history-search').value.trim().toLowerCase();
  const jobs = lab.jobs.filter(j => [j.prompt, j.model_name, j.settings?.lora_name || ''].join(' ').toLowerCase().includes(query));
  $('#lab-history-count').textContent = `最近 ${lab.jobs.length} 条 · 选中 ${lab.compare.size} 项对比`;
  const signature = JSON.stringify([jobs, Array.from(lab.compare), Array.from(lab.selection), lab.trash]);
  if(signature === lab.historySignature) return;
  lab.historySignature = signature;
  const list = $('#lab-history-list');
  list.innerHTML = jobs.length ? jobs.map(j => `
    <article class="lab-history-item">
      <button class="lab-history-preview" type="button" data-preview="${escapeHTML(j.id)}" aria-label="${j.kind==='video'&&j.status==='done'?'播放视频':'查看任务'} ${escapeHTML(j.id.slice(0,8))}">
        ${j.status==='done' ? (j.kind==='image' ? `<img src="${escapeHTML(j.asset_url)}" alt="生成结果" loading="lazy">` : `${j.thumbnail_url ? `<img src="${escapeHTML(j.thumbnail_url)}" alt="视频缩略图" loading="lazy" data-video-thumbnail>` : ''}<span class="lab-video-play" aria-hidden="true"><span>▶</span></span><span class="lab-video-duration" aria-hidden="true">${escapeHTML(j.settings?.seconds ? '约 ' + j.settings.seconds + ' 秒' : '播放视频')}</span>`) : escapeHTML(labStatuses[j.status] || j.status)}
      </button>
      <div class="lab-history-copy">
        <p>${escapeHTML(j.image_skill?.user_prompt || j.input_prompt || j.prompt)}</p>
        <small>${escapeHTML(j.model_name)} · ${escapeHTML(labStatuses[j.status] || j.status)}</small>
        <small>${escapeHTML(jobSummary(j))}</small>
        <small>${escapeHTML(labDate(j.created_at))} · ${j.origin==='lab' ? '独立页' : 'AI 助手'}</small>
        ${j.status==='done'&&!lab.trash ? `<label><input type="checkbox" data-compare="${escapeHTML(j.id)}" ${lab.compare.has(j.id)?'checked':''}>加入对比</label>` : ''}
        ${labHistoryActions(j)}
      </div>
    </article>
  `).join('') : '<p class="lab-muted">这里还没有记录。在左侧填写提示词并开始测试生成。</p>';
  bindLabManagement(list);
  list.querySelectorAll('[data-video-thumbnail]').forEach(img => img.onerror = () => { img.hidden = true; });
  list.querySelectorAll('[data-preview]').forEach(b => b.onclick = () => {
    const j = lab.jobs.find(x => x.id === b.dataset.preview);
    if(!j) return;
    renderLabJob(j);
    $('.lab-result')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if(j.kind === 'video' && j.status === 'done'){
      const video = $('#lab-preview video');
      document.querySelectorAll('video').forEach(other => { if(other !== video) other.pause(); });
      video.play().catch(() => toast('请点击播放器的播放按钮继续播放'));
    }
  });
  list.querySelectorAll('[data-compare]').forEach(box => box.onchange = () => {
    if(box.checked && lab.compare.size >= 4){ box.checked = false; toast('最多同时比较 4 项'); return; }
    if(box.checked) lab.compare.add(box.dataset.compare); else lab.compare.delete(box.dataset.compare);
    renderLabCompare();
    $('#lab-history-count').textContent = `最近 ${lab.jobs.length} 条 · 选中 ${lab.compare.size} 项对比`;
  });
}

function renderLabCompare(){
  const jobs = [...lab.compare].map(id => lab.jobs.find(j => j.id === id)).filter(Boolean);
  $('#lab-compare').innerHTML = jobs.length ? jobs.map(j => `
    <figure>
      ${j.kind === 'image' ? `<img src="${escapeHTML(j.asset_url)}" alt="对比结果">` : `<video src="${escapeHTML(j.asset_url)}" controls preload="metadata"></video>`}
      <figcaption>${escapeHTML(j.model_name)}<br>${escapeHTML(jobSummary(j))}</figcaption>
    </figure>
  `).join('') : '<p class="lab-muted">选择完成的结果进行对比。</p>';
}

function updateGenerateState(){
  const prompt = ($('#lab-prompt')?.value || '').trim();
  const segments = parseSequencePrompt(prompt);
  const isSeq = segments.length > 1;
  const totalSec = isSeq ? segments.reduce((sum, s) => sum + (s.seconds || 5), 0) : 0;

  const btn = $('#lab-generate');
  if(!btn) return;
  btn.disabled = lab.submitting || lab.busy || lab.uploading;
  if(lab.uploading){
    btn.textContent = '正在上传参考图…';
  } else if(lab.submitting){
    btn.textContent = '正在提交至 4090 算力…';
  } else if(lab.busy){
    btn.textContent = '已有任务进行中';
  } else if(isSeq){
    btn.textContent = `🚀 开始生成 ${totalSec.toFixed(0)}s 连续微短剧 (${segments.length}镜头)`;
  } else {
    btn.textContent = '生成视频 ↑';
  }
}

// ----------------------------------------------------
// 彻底清空全部参考图（彻底解决死灰复燃问题）
// ----------------------------------------------------
function clearAllVideoReferences(){
  window.labUserClearedRefs = true;
  if(typeof videoInputs !== 'undefined'){
    for(const item of [...(videoInputs.images || [])]){
      item.removed = true;
      item.controller?.abort();
    }
    videoInputs.images = [];
    for(const key of ['first', 'last']){
      if(videoInputs[key]){
        videoInputs[key].removed = true;
        videoInputs[key].controller?.abort();
        videoInputs[key] = null;
      }
    }
    for(const key of ['videos', 'audios']){
      if(Array.isArray(videoInputs[key])){
        for(const item of videoInputs[key]){ item.removed = true; item.controller?.abort(); }
        videoInputs[key] = [];
      }
    }
  }
  // 同步清空 localStorage 草稿
  try{
    const draft = readLabDraft() || {};
    draft.references = { images: [] };
    draft.draftreference_media = { references: { images: [] }, media: {} };
    delete draft.reference_media;
    localStorage.setItem(labStorage, JSON.stringify(draft));
  }catch{}

  renderVideoRefs();
  syncVideoInputState();
  toast('已彻底清空全部参考图，草稿已清除！');
}

// 拦截并强化单个卡片的移除动作
function enhanceReferenceRemoval(){
  const container = $('#lab-video-refs');
  if(!container) return;
  container.addEventListener('click', e => {
    const btn = e.target.closest('[data-video-remove]');
    if(btn){
      window.labUserClearedRefs = true;
      setTimeout(() => {
        try{
          const draft = readLabDraft() || {};
          draft.references = { images: (videoInputs?.images || []).map(x => x.id).filter(Boolean) };
          draft.draftreference_media = { references: draft.references, media: {} };
          localStorage.setItem(labStorage, JSON.stringify(draft));
        }catch{}
      }, 50);
    }
  });
}

// ----------------------------------------------------
// ----------------------------------------------------
// 4 幕微短剧导演台 (Act 1 ~ Act 4 工业级流水线)
// ----------------------------------------------------
function saveDramaState(){
  try{
    localStorage.setItem(DRAMA_STORAGE, JSON.stringify({
      preset: currentDramaPreset,
      acts: actsState
    }));
  }catch(e){
    console.warn('saveDramaState failed:', e);
  }
}

function restoreDramaState(){
  try{
    const raw = localStorage.getItem(DRAMA_STORAGE);
    if(raw){
      const data = JSON.parse(raw);
      if(data.preset && DRAMA_PRESETS[data.preset]){
        currentDramaPreset = data.preset;
      }
      if(Array.isArray(data.acts) && data.acts.length === 4){
        actsState = data.acts.map(a => ({
          ...a,
          status: (a.job && a.job.status === 'done') ? 'done' : 'idle',
          elapsed: 0
        }));
        return;
      }
    }
  }catch(e){
    console.warn('restoreDramaState failed:', e);
  }
  loadDramaPreset(currentDramaPreset, true);
}

function syncActsFromServerJobs(jobs){
  if(!Array.isArray(jobs) || !jobs.length) return;
  let changed = false;
  const norm = s => String(s || '').trim().replace(/\s+/g, ' ');
  for(let i = 0; i < actsState.length; i++){
    const act = actsState[i];
    const targetPrompt = norm(act.prompt);
    if(!targetPrompt) continue;
    const match = jobs.find(j => j.kind === 'video' && !j.deleted_at && j.prompt && norm(j.prompt) === targetPrompt);
    if(!match) continue;
    if(match.status === 'done'){
      if(act.status !== 'done' || !act.job || act.job.id !== match.id){
        act.job = match;
        act.status = 'done';
        changed = true;
      }
    } else if(['connecting','queued','running','downloading','reconnecting'].includes(match.status)){
      if(act.status !== 'running' || !act.job || act.job.id !== match.id){
        act.job = match;
        act.status = 'running';
        changed = true;
      }
    }
  }
  if(changed){
    saveDramaState();
    renderActCards();
  }
}

function syncRawScriptTextareaFromActs(){
  const rawTa = $('#aicg3d-raw-textarea');
  const labPrompt = $('#lab-prompt');
  const fullText = actsState.map(a => (a.prompt || '').trim()).filter(Boolean).join('\n\n');
  if(rawTa) rawTa.value = fullText;
  if(labPrompt) labPrompt.value = fullText;
}

function loadDramaPreset(presetKey, isInit=false){
  if(!DRAMA_PRESETS[presetKey]) return;
  currentDramaPreset = presetKey;
  const p = DRAMA_PRESETS[presetKey];
  actsState = JSON.parse(JSON.stringify(p.acts)).map(a => ({
    ...a, job: null, status: 'idle', elapsed: 0
  }));
  if(p.skill_id && $('#aicg3d-skill-select')){
    $('#aicg3d-skill-select').value = p.skill_id;
  }
  if(p.storyIdea && $('#aicg3d-story-input')){
    $('#aicg3d-story-input').value = p.storyIdea;
  }
  syncRawScriptTextareaFromActs();
  if(lab.jobs && lab.jobs.length){
    syncActsFromServerJobs(lab.jobs);
  }
  if(Array.isArray(p.defaultReferenceJobs) && p.defaultReferenceJobs.length && !window.labUserClearedRefs && typeof safeAttachVideoGalleryImage === 'function'){
    (async () => {
      try {
        const curIds = (typeof videoInputs !== 'undefined' && videoInputs.images) ? videoInputs.images.filter(i => !i.error && !i.removed).map(i => i.source_job || i.id) : [];
        if(curIds.length < p.defaultReferenceJobs.length){
          if(typeof videoInputs !== 'undefined'){
            for(const item of [...(videoInputs.images || [])]){ item.removed = true; }
            videoInputs.images = [];
          }
          ensureR2VMode();
          for(const jid of p.defaultReferenceJobs){
            await safeAttachVideoGalleryImage('images', jid);
          }
          rememberLab();
          updateSequenceUI();
        }
      } catch(e) {
        console.warn('预设参考图挂载跳过:', e);
      }
    })();
  }
  saveDramaState();
  renderActCards();
  if(!isInit){
    toast(`已载入【${p.name}】12 段连续微短剧剧本！`);
  }
}

function renderActCards(){
  const container = $('#act-cards-container');
  if(!container) return;

  // 更新预设按钮激活态
  document.querySelectorAll('.drama-preset-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.preset === currentDramaPreset);
  });

  const doneActs = actsState.filter(a => a.status === 'done' || (a.job && a.job.status === 'done'));
  const runningAct = actsState.find(a => a.status === 'running');
  const doneCount = doneActs.length;
  const segSec = getSelectedSeqSeconds();
  const subCountPerAct = segSec <= 5.5 ? 3 : (segSec <= 10.5 ? 2 : 1);
  const totalSegCount = subCountPerAct * 4;
  const refImgSize = getSelectedRefImageSize();
  const chkVal = $('#lab-checkpoint')?.value || 'hybrid';
  const accVal = $('#lab-accelerator')?.value || 'none';
  const chkLabel = chkVal === 'hybrid' ? 'Hybrid b25-49' : (chkVal === 'singularity' ? 'Singularity' : chkVal);
  const accLabel = accVal === 'none' ? '0-LoRA 25步' : (accVal === 'ref8' ? 'Turbo 8步' : 'Turbo 4步');

  // 更新总控状态
  const statusEl = $('#drama-pipeline-status');
  if(statusEl && !runningAct){
    statusEl.innerHTML = `🎬 方案配置：<strong>${chkLabel} · ${accLabel} · ref=${refImgSize} · ${totalSegCount}镜×${segSec}s = 60s 单次直出</strong> · 已就绪 ${doneCount}/4 幕`;
  }
  const runAllBtn = $('#drama-run-all-btn');
  if(runAllBtn && !runningAct){
    runAllBtn.textContent = `⚡ 单次直出 60s 连续长视频 (${totalSegCount}镜×${segSec}s · ${chkLabel} · ${accLabel})`;
  }

  // 更新合并按钮
  const concatBtn = $('#drama-concat-btn');
  if(concatBtn){
    concatBtn.disabled = doneCount < 2 || !!runningAct;
    concatBtn.classList.toggle('pulse', doneCount === 4 && !runningAct);
    concatBtn.textContent = doneCount === 4 ? `🎞️ 4 幕齐备！一键合并导出完整短剧` : (doneCount >= 2 ? `🎞️ 合并已就绪的 ${doneCount} 幕短剧` : '🎞️ 分幕拼接备用 (FFmpeg)');
  }

  // 渲染每个 Act 卡片
  container.innerHTML = actsState.map((act, idx) => {
    let statusBadge = `<span class="act-status-badge act-status-idle">⏳ 待渲染 (15s·${subCountPerAct}镜)</span>`;
    if(act.status === 'running'){
      statusBadge = `<span class="act-status-badge act-status-running">⚙️ 4090 渲染中 (${act.elapsed || 0}s)</span>`;
    }else if(act.status === 'done'){
      statusBadge = `<span class="act-status-badge act-status-done">✅ 已就绪 (15s)</span>`;
    }else if(act.status === 'error'){
      statusBadge = '<span class="act-status-badge act-status-error">❌ 生成失败</span>';
    }

    const videoHtml = (act.status === 'done' && act.job && act.job.status === 'done') ? `
      <div class="act-video-preview">
        <video src="/generation-assets/${act.job.id}" controls playsinline preload="metadata" loop></video>
      </div>
    ` : '';

    const btnText = act.status === 'running' ? '⏳ 渲染中…' : (act.status === 'done' ? `🔄 单独重抽本幕` : `🎬 单独测试本幕`);
    const btnDisabled = !!runningAct || lab.busy;

    return `
      <div class="act-card ${act.status === 'running' ? 'active-rendering' : ''} ${act.status === 'done' ? 'completed' : ''}" data-act-index="${idx}">
        <div class="act-card-head">
          <div style="display:flex;align-items:center;gap:6px;">
            <span class="act-badge">Act ${act.id}</span>
            <strong style="font-size:11px;color:#0f172a;">${escapeHTML(act.title)}</strong>
            <span style="font-size:10px;color:#64748b;">${escapeHTML(act.subtitle || '')}</span>
          </div>
          ${statusBadge}
        </div>
        <textarea class="act-prompt-input" data-act-index="${idx}" rows="4" placeholder="填写本幕提示词...">${escapeHTML(act.prompt)}</textarea>
        ${videoHtml}
        <div class="act-card-actions">
          <button type="button" class="act-render-btn" data-act-index="${idx}" ${btnDisabled ? 'disabled' : ''}>
            ${btnText}
          </button>
          <span style="font-size:9.5px;color:#94a3b8;">${chkLabel} · ${accLabel} · ref=${refImgSize} · ${subCountPerAct}镜×${segSec}s=15s</span>
        </div>
      </div>
    `;
  }).join('');

  // 绑定 textarea 输入同步
  container.querySelectorAll('.act-prompt-input').forEach(ta => {
    ta.oninput = (e) => {
      const idx = Number(e.target.dataset.actIndex);
      if(actsState[idx]){
        actsState[idx].prompt = e.target.value;
        saveDramaState();
      }
    };
  });

  // 绑定单幕生成按钮
  container.querySelectorAll('.act-render-btn').forEach(btn => {
    btn.onclick = (e) => {
      const idx = Number(e.currentTarget.dataset.actIndex);
      generateSingleAct(idx);
    };
  });
}

async function generateSingleAct(idx){
  const act = actsState[idx];
  if(!act || act.status === 'running') return;
  if(lab.busy){
    toast('4090 正在执行其他生成任务，请等待或停止当前任务');
    return;
  }

  const promptText = (act.prompt || '').trim();
  if(!promptText){
    toast(`第 ${idx + 1} 幕提示词不能为空`);
    return;
  }

  act.status = 'running';
  act.elapsed = 0;
  renderActCards();

  const timer = setInterval(() => {
    act.elapsed = (act.elapsed || 0) + 1;
    const badge = $(`[data-act-index="${idx}"] .act-status-badge`);
    if(badge) badge.textContent = `⚙️ 4090 渲染中 (${act.elapsed}s)`;
  }, 1000);

  try {
    // 检查角色绑定，若尚未绑定且未主动清空则自动挂载最新定妆照
    let validImages = (typeof videoInputs !== 'undefined' && videoInputs.images) ? videoInputs.images.filter(i => !i.error && !i.removed) : [];
    let charId = validImages[0]?.id;
    if(!charId && !window.labUserClearedRefs && typeof safeAttachVideoGalleryImage === 'function'){
      try {
        const imgData = await api('/api/generation/lab?kind=image&scope=all&trash=0');
        const doneImgs = (imgData?.jobs || []).filter(j => j.status === 'done' && !j.deleted_at);
        const latestImg = doneImgs.find(j => (j.prompt || '').includes('定妆照')) || doneImgs.find(j => j.origin === 'lab') || doneImgs[0];
        if(latestImg){
          const attached = await safeAttachVideoGalleryImage('images', latestImg.id);
          charId = attached?.id;
        }
      } catch(autoAttachErr) {
        console.warn('自动挂载最新定妆照跳过:', autoAttachErr);
      }
    }
    const hasChar = !!charId;
    if(hasChar){
      ensureR2VMode();
    }

    // 确保分辨率严格满足 32 倍数对齐（兼容 r2v 与竖屏短剧）
    let rawSize = $('#media-size')?.value || '448x768';
    try { rawSize = selectedMediaSize('media', hasChar ? 'r2v' : 't2v') || rawSize; } catch {}
    if(rawSize === '768x432' || rawSize === '432x768'){
      rawSize = promptText.toLowerCase().includes('vertical') ? '448x768' : '768x448';
    }
    const sizeParts = String(rawSize).split('x').map(Number);
    if(sizeParts.length !== 2 || sizeParts.some(n => !n || n % 32 !== 0)){
      rawSize = promptText.toLowerCase().includes('vertical') ? '448x768' : '768x448';
    }

    const segSec = getSelectedSeqSeconds();
    const segFc = getSelectedFramesForSeconds(segSec);
    const refImgSize = getSelectedRefImageSize();
    const selectedCheckpoint = $('#lab-checkpoint')?.value || 'hybrid';
    const selectedAccelerator = $('#lab-accelerator')?.value || 'none';

    const payload = {
      model: 'h3',
      prompt: promptText,
      size: rawSize,
      workflow: hasChar ? 'r2v' : 't2v',
      video_checkpoint: selectedCheckpoint,
      video_accelerator: selectedAccelerator,
      video_ref_image_size: refImgSize,
      frames: segFc,
      seed: $('#lab-seed')?.value || '',
      request_id: crypto.randomUUID(),
      ...(hasChar ? { references: { images: [charId] } } : {})
    };

    const initialJob = await api('/api/generation/lab/start', payload);
    const jobId = initialJob.id;
    let job = initialJob;

    // 轮询直至完成
    for(let i=0; i<180; i++){
      await new Promise(r => setTimeout(r, 2000));
      try {
        job = await api('/api/generation/job/' + jobId);
      } catch(netErr) {
        console.warn('轮询状态网络抖动，继续重试:', netErr);
      }
      if(job.status === 'uncertain'){
        try {
          job = await api('/api/generation/job/' + jobId + '/reconcile', {});
        } catch(recErr) {
          console.warn('自动对账失败:', recErr);
        }
      }
      if(job.status === 'done' || job.status === 'error' || job.status === 'cancelled') break;
    }

    if(job.status === 'done'){
      act.job = job;
      act.status = 'done';
      saveDramaState();
      toast(`🎉 第 ${idx + 1} 幕渲染成功！耗时 ${act.elapsed} 秒`);
      await refreshLab();
    } else {
      act.status = 'error';
      toast(`❌ 第 ${idx + 1} 幕生成未成功 (状态: ${job.status || 'unknown'})`);
    }
  } catch(err) {
    act.status = 'error';
    toast(`生成失败: ${err.message}`);
  } finally {
    clearInterval(timer);
    renderActCards();
  }
}

async function runAllActsSequence(){
  if(actsState.some(a => a.status === 'running') || lab.busy){
    toast('当前正有任务在执行，请等待其完成后再启动 AICG3D 无限流');
    return;
  }
  const validActs = actsState.filter(a => (a.prompt || '').trim());
  if(validActs.length < 2){
    toast('请至少填写 2 幕剧情提示词');
    return;
  }

  const runBtn = $('#drama-run-all-btn');
  const statusEl = $('#drama-pipeline-status');
  if(runBtn) runBtn.disabled = true;

  let elapsed = 0;
  actsState[0].status = 'running';
  actsState[0].elapsed = 0;
  for(let i=1; i<actsState.length; i++){
    actsState[i].status = 'idle';
    actsState[i].elapsed = 0;
  }
  renderActCards();

  const timer = setInterval(() => {
    elapsed += 1;
    const runningIdx = actsState.findIndex(a => a.status === 'running');
    if(runningIdx >= 0){
      actsState[runningIdx].elapsed = (actsState[runningIdx].elapsed || 0) + 1;
      const badge = $(`[data-act-index="${runningIdx}"] .act-status-badge`);
      if(badge) badge.textContent = `⚙️ 尾帧续写中 (${actsState[runningIdx].elapsed}s)`;
    }
  }, 1000);

  try {
    const segSec = getSelectedSeqSeconds();
    const refImgSize = getSelectedRefImageSize();
    const selectedCheckpoint = $('#lab-checkpoint')?.value || 'hybrid';
    const selectedAccelerator = $('#lab-accelerator')?.value || 'none';

    const segments = buildSixtySecondSegments(validActs, segSec);
    const combinedPrompt = segments.map(s => `【第${s.index}段｜${s.seconds}秒】\n${s.prompt}`).join('\n\n');
    const needsTwoChars = /<Picture\s*2>/i.test(combinedPrompt);
    const requiredRefs = needsTwoChars ? 2 : 1;

    let validImages = (typeof videoInputs !== 'undefined' && videoInputs.images) ? videoInputs.images.filter(i => !i.error && !i.removed) : [];
    let charIds = validImages.map(i => i.id).filter(Boolean);

    if(charIds.length < requiredRefs && !window.labUserClearedRefs && typeof generateCastImagesForCurrentDrama === 'function'){
      toast(needsTwoChars ? '🎭 检测到双人剧本 (<Picture 1> + <Picture 2>)，正在自动生成双角色定妆照锁定两人外貌…' : '🎭 正在自动生成主角定妆照锁定外貌…');
      await generateCastImagesForCurrentDrama(combinedPrompt, requiredRefs);
      validImages = (typeof videoInputs !== 'undefined' && videoInputs.images) ? videoInputs.images.filter(i => !i.error && !i.removed) : [];
      charIds = validImages.map(i => i.id).filter(Boolean);
    }

    const hasChar = charIds.length > 0;
    if(hasChar){
      ensureR2VMode();
    }

    let rawSize = $('#media-size')?.value || '448x768';
    try { rawSize = selectedMediaSize('media', hasChar ? 'r2v' : 't2v') || rawSize; } catch {}
    if(rawSize === '768x432' || rawSize === '432x768'){
      rawSize = combinedPrompt.toLowerCase().includes('vertical') ? '448x768' : '768x448';
    }
    const sizeParts = String(rawSize).split('x').map(Number);
    if(sizeParts.length !== 2 || sizeParts.some(n => !n || n % 32 !== 0)){
      rawSize = combinedPrompt.toLowerCase().includes('vertical') ? '448x768' : '768x448';
    }

    const selectedSampling = $('#lab-video-sampling')?.value || 'single';
    const payload = {
      model: 'h3',
      prompt: combinedPrompt,
      size: rawSize,
      workflow: hasChar ? 'r2v' : 't2v',
      video_checkpoint: selectedCheckpoint,
      video_accelerator: selectedAccelerator,
      video_ref_image_size: refImgSize,
      video_sampling: selectedSampling,
      ...(selectedSampling === 'dual' ? {
        video_refine_steps: Number($('#lab-video-refine-steps')?.value || 4),
        video_refine_denoise: Number($('#lab-video-refine-denoise')?.value || 0.35),
        video_upscale: Number($('#lab-video-upscale')?.value || 1.5),
      } : {}),
      frames: segments.reduce((sum, s) => sum + s.frameCount, 0),
      seed: $('#lab-seed')?.value || '',
      is_sequence: true,
      segments: segments,
      request_id: crypto.randomUUID(),
      ...(hasChar ? { references: { images: charIds } } : {})
    };

    toast(`⚡ 已启动 AICG3D 13号无限流 (${selectedCheckpoint} · ${selectedAccelerator} · ${selectedSampling === 'dual' ? '双采精修' : '单采'} · ref=${refImgSize} · ${charIds.length}张参考图 · ${segments.length}镜单次直出 60s)！`);
    const initialJob = await api('/api/generation/lab/start', payload);
    const jobId = initialJob.id;
    let job = initialJob;
    lab.selected = job;
    renderLabJob(job);

    const segsPerAct = Math.max(1, Math.round(segments.length / actsState.length));
    for(let i=0; i<2400; i++){
      await new Promise(r => setTimeout(r, 2000));
      try {
        job = await api('/api/generation/job/' + jobId);
      } catch(netErr) {
        console.warn('轮询状态网络抖动，继续重试:', netErr);
        continue;
      }
      lab.selected = job;
      renderLabJob(job);

      const progNode = job.progress?.node || '';
      const progLabel = job.progress?.label || '';
      const progPct = job.progress?.percent != null ? `${job.progress.percent}%` : '';
      if(progNode.startsWith('seq_seg_')){
        const segNum = parseInt(progNode.split('_').pop(), 10) || 1;
        const activeIdx = Math.min(actsState.length - 1, Math.max(0, Math.floor((segNum - 1) / segsPerAct)));
        actsState.forEach((a, idx) => {
          if(idx < activeIdx) a.status = 'done';
          else if(idx === activeIdx) a.status = 'running';
          else a.status = 'idle';
        });
        renderActCards();
      }
      if(statusEl && progLabel){
        statusEl.innerHTML = `⚡ AICG3D 60s 无限流直出中：<strong>${escapeHTML(progLabel)}</strong> ${progPct ? '(' + progPct + ')' : ''} · 已用时 ${elapsed}s`;
      }

      if(job.status === 'uncertain'){
        try {
          job = await api('/api/generation/job/' + jobId + '/reconcile', {});
        } catch(recErr) {
          console.warn('自动对账失败:', recErr);
        }
      }
      if(job.status === 'done' || job.status === 'error' || job.status === 'cancelled') break;
    }

    if(job.status === 'done'){
      actsState.forEach(a => { a.status = 'done'; });
      saveDramaState();
      lab.selected = job;
      renderLabJob(job);
      await refreshLab();
      if(statusEl){
        statusEl.innerHTML = `✅ <strong>AICG3D 13号无限流 60s 单次直出完成！</strong> 总耗时 ${elapsed}s · ${segments.length}镜尾帧 Latent 续写 · ref=${refImgSize} 全程锁脸`;
      }
      toast(`🎉 AICG3D 13号无限流 60 秒连续长视频单次直出成功！耗时 ${elapsed} 秒`);
    } else {
      const runningAct = actsState.find(a => a.status === 'running');
      if(runningAct) runningAct.status = 'error';
      toast(`❌ 无限流生成未成功 (状态: ${job.status || 'unknown'}): ${job.error || ''}`);
    }
  } catch(err) {
    const runningAct = actsState.find(a => a.status === 'running');
    if(runningAct) runningAct.status = 'error';
    toast(`AICG3D 无限流启动失败: ${err.message}`);
  } finally {
    clearInterval(timer);
    if(runBtn) runBtn.disabled = false;
    renderActCards();
  }
}

async function concatFullDrama(){
  const doneActs = actsState.filter(a => a.job && a.job.status === 'done');
  if(doneActs.length < 2){
    toast('请至少先完成 2 幕视频生成再合并！');
    return;
  }
  const btn = $('#drama-concat-btn');
  if(btn){
    btn.disabled = true;
    btn.textContent = '⏳ 正在通过 ffmpeg 瞬间合并短剧…';
  }
  try {
    const jobIds = doneActs.map(a => a.job.id);
    const presetName = DRAMA_PRESETS[currentDramaPreset]?.name || '微短剧';
    const result = await api('/api/generation/lab/concat', {
      job_ids: jobIds,
      title: `【完整短剧】${presetName}`
    });
    toast('🎉 微短剧成片瞬间合并完成！已载入右侧大屏播放器！');
    lab.selected = result;
    renderLabJob(result);
    await refreshLab();
  } catch(err) {
    toast('合并失败：' + err.message);
  } finally {
    if(btn){
      btn.disabled = false;
      renderActCards();
    }
  }
}

function updateSequenceUI(){
  renderActCards();
}

function applyCompiledSegmentsToActs(segments, actTitles){
  const segSec = getSelectedSeqSeconds() || 5;
  const perAct = Math.max(1, Math.ceil(segments.length / 4));
  const defaultTitles = [
    ['第一幕·建立空间 (0-15s)', '同空间连续推进 · 段1~3'],
    ['第二幕·核心事件 (15-30s)', '动作钩子无缝接力 · 段4~6'],
    ['第三幕·情绪高潮 (30-45s)', '单说话人标准对白 · 段7~9'],
    ['第四幕·动作收束 (45-60s)', '同场景定格收尾 · 段10~12']
  ];
  actsState = [0, 1, 2, 3].map(actIdx => {
    const slice = segments.slice(actIdx * perAct, (actIdx + 1) * perAct);
    const promptBody = slice.map((seg, sIdx) => {
      const globalNum = actIdx * perAct + sIdx + 1;
      const cleanSeg = String(seg || '').replace(/^【第\d+段[｜\|][\d.]+秒】\s*/i, '').trim();
      return `【第${globalNum}段｜${segSec}秒】\n${cleanSeg}`;
    }).join('\n\n');
    return {
      id: actIdx + 1,
      title: (actTitles && actTitles[actIdx]) ? actTitles[actIdx] : defaultTitles[actIdx][0],
      subtitle: defaultTitles[actIdx][1],
      prompt: promptBody,
      job: null,
      status: 'idle',
      elapsed: 0
    };
  });
  syncRawScriptTextareaFromActs();
  saveDramaState();
  renderActCards();
}

async function initAicg3dCompilerControls(){
  const skillSelect = $('#aicg3d-skill-select');
  const storyInput = $('#aicg3d-story-input');
  const compileBtn = $('#aicg3d-compile-btn');
  const statusSpan = $('#aicg3d-compile-status');
  const toggleRawBtn = $('#aicg3d-toggle-raw-btn');
  const rawDrawer = $('#aicg3d-raw-drawer');
  const rawTextarea = $('#aicg3d-raw-textarea');
  const parseRawBtn = $('#aicg3d-parse-raw-btn');
  const copyRawBtn = $('#aicg3d-copy-raw-btn');

  // 从后端拉取 comfyui-AICG3D 完整 21 个导演 Skills 目录
  try {
    const data = await api('/api/generation/lab/aicg3d-presets');
    if(skillSelect && Array.isArray(data?.skills) && data.skills.length){
      const curVal = skillSelect.value || 'short-scene-director-lite';
      skillSelect.innerHTML = data.skills.map(s =>
        `<option value="${escapeHTML(s.id)}">${escapeHTML(s.name)} (${escapeHTML(s.tag || s.id)})</option>`
      ).join('');
      if([...skillSelect.options].some(o => o.value === curVal)){
        skillSelect.value = curVal;
      }
    }
  } catch(e) {
    console.warn('加载 AICG3D Skills 列表跳过:', e);
  }

  if(toggleRawBtn && rawDrawer){
    toggleRawBtn.onclick = () => {
      rawDrawer.hidden = !rawDrawer.hidden;
      if(!rawDrawer.hidden){
        syncRawScriptTextareaFromActs();
      }
    };
  }

  if(copyRawBtn && rawTextarea){
    copyRawBtn.onclick = async () => {
      syncRawScriptTextareaFromActs();
      try {
        await navigator.clipboard.writeText(rawTextarea.value);
        toast('已复制整篇【第1~12段】AICG3D 分段提示词！');
      } catch {
        rawTextarea.select();
        toast('已选中全文，请按 Cmd+C 复制');
      }
    };
  }

  if(parseRawBtn && rawTextarea){
    parseRawBtn.onclick = async () => {
      const rawText = (rawTextarea.value || '').trim();
      if(!rawText){
        toast('请先在文本框中粘贴或输入【第1段｜5秒】格式的分段剧本');
        return;
      }
      try {
        const res = await api('/api/generation/lab/aicg3d-parse', {
          raw_script: rawText,
          segment_seconds: getSelectedSeqSeconds() || 5,
          has_ref_image: true
        });
        if(Array.isArray(res?.segments) && res.segments.length){
          applyCompiledSegmentsToActs(res.segments);
          if(statusSpan){
            statusSpan.textContent = `✅ 已解析并填入 ${res.segments.length} 段连贯提示词（13项自查清洗已通过）`;
          }
          toast(`🎉 已成功解析 ${res.segments.length} 段 AICG3D 剧本并填入 4 幕！`);
        }
      } catch(err) {
        toast('解析失败：' + err.message);
      }
    };
  }

  if(compileBtn && storyInput){
    compileBtn.onclick = async () => {
      const story = (storyInput.value || '').trim();
      if(!story){
        toast('请先输入一句话故事创意或剧情构想');
        storyInput.focus();
        return;
      }
      const skillId = skillSelect?.value || 'short-scene-director-lite';
      const segSec = getSelectedSeqSeconds() || 5;
      const segCount = segSec <= 5.5 ? 12 : (segSec <= 10.5 ? 8 : 4);
      compileBtn.disabled = true;
      const origText = compileBtn.textContent;
      compileBtn.textContent = '⏳ 正在按原装模板V1编译 12 段剧本…';
      if(statusSpan){
        statusSpan.innerHTML = '<span style="color:#d97706;font-weight:600;">⚙️ 正在调用 LLM 执行《无限视频生成提示词模板V1.MD》+ 所选导演 Skill 编译（约 8~15 秒）…</span>';
      }
      try {
        const res = await api('/api/generation/lab/aicg3d-compile', {
          story: story,
          skill_id: skillId,
          segment_count: segCount,
          segment_seconds: Math.round(segSec),
          has_ref_image: true,
          character_desc: DRAMA_PRESETS[currentDramaPreset]?.characterPrompt || ''
        });
        if(Array.isArray(res?.segments) && res.segments.length){
          applyCompiledSegmentsToActs(res.segments);
          if(rawTextarea) rawTextarea.value = res.rawScript || '';
          if(statusSpan){
            statusSpan.innerHTML = `<span style="color:#15803d;font-weight:600;">✅ 原装编译器已生成 ${res.segments.length} 段×${Math.round(segSec)}s 连贯提示词（Skill: ${escapeHTML(skillId)} · 同空间动作钩子咬合 · 13项自查通过）</span>`;
          }
          toast(`🎉 AICG3D 原装编译器已生成 ${res.segments.length} 段强连贯剧本并填入 4 幕！`);
        }
      } catch(err) {
        if(statusSpan){
          statusSpan.innerHTML = `<span style="color:#dc2626;">❌ 编译失败：${escapeHTML(err.message)}</span>`;
        }
        toast('编译失败：' + err.message);
      } finally {
        compileBtn.disabled = false;
        compileBtn.textContent = origText;
      }
    };
  }
}

function initSequenceControls(){
  // 绑定预设切换按钮
  document.querySelectorAll('.drama-preset-btn').forEach(btn => {
    btn.onclick = () => {
      loadDramaPreset(btn.dataset.preset);
    };
  });

  // 绑定参考图尺寸与单幕时长切换
  for(const selId of ['lab-ref-image-size', 'lab-seq-seg-seconds', 'lab-checkpoint', 'lab-accelerator']){
    const el = $('#' + selId);
    if(el){
      el.addEventListener('change', () => {
        rememberLab();
        renderActCards();
      });
    }
  }

  // 绑定重置与清空
  const resetBtn = $('#drama-reset-btn');
  if(resetBtn){
    resetBtn.onclick = () => {
      loadDramaPreset(currentDramaPreset);
    };
  }
  const clearBtn = $('#drama-clear-btn');
  if(clearBtn){
    clearBtn.onclick = () => {
      actsState.forEach(a => { a.prompt = ''; a.job = null; a.status = 'idle'; });
      syncRawScriptTextareaFromActs();
      saveDramaState();
      renderActCards();
      toast('4 幕剧本已清空');
    };
  }

  // 绑定全剧流水线与合并
  const runAllBtn = $('#drama-run-all-btn');
  if(runAllBtn){
    runAllBtn.onclick = () => runAllActsSequence();
  }
  const concatBtn = $('#drama-concat-btn');
  if(concatBtn){
    concatBtn.onclick = () => concatFullDrama();
  }

  restoreDramaState();
  syncRawScriptTextareaFromActs();
  renderActCards();
  initAicg3dCompilerControls();
}

// ----------------------------------------------------
// 依据剧本自动提取人设并生成主角定妆照 (Qwen 2.1 高清)
// ----------------------------------------------------
function extractCharacterPrompts(scriptText, requiredCount=1){
  const preset = DRAMA_PRESETS[currentDramaPreset];
  if(preset && Array.isArray(preset.characterPrompts) && preset.characterPrompts.length >= requiredCount){
    return preset.characterPrompts.slice(0, requiredCount);
  }
  if(preset && preset.characterPrompt && requiredCount === 1){
    return [preset.characterPrompt];
  }
  if(!scriptText){
    scriptText = actsState.map(a => a.prompt).join('\n');
  }
  if(requiredCount >= 2 || /<Picture\s*2>/i.test(scriptText)){
    return [
      '16:9古风奇幻电影定妆照，云雾缭绕的仙境白玉平台，二十岁出头的古风仙侠清丽女弟子，身穿素白飘逸古风仙门长袍，发髻佩戴银色发冠，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官精致清晰锐利，数字电影摄影机成像，8k超清',
      '16:9古风奇幻电影定妆照，云雾缭绕的仙境白玉平台，二十五岁的古风仙侠俊朗男大师兄，身穿淡青蓝色古风仙门长袍，束发银冠，神情温和带笑意，斜上方柔和绚丽的仙境霞光均匀打亮面部轮廓，五官清晰锐利，数字电影摄影机成像，8k超清'
    ];
  }
  let desc = '';
  const lines = scriptText.split('\n').map(l => l.trim()).filter(Boolean);
  for(const l of lines){
    if(l.includes('Subject 1') || l.includes('主角') || l.includes('探长') || l.includes('侠客') || l.includes('刺客') || l.includes('Sam') || l.includes('年轻')){
      desc += l.replace(/^(?:subject_definitions:|integrated_multimodal_description:|[【\[].*?[】\]])/gi, '') + ' ';
    }
  }
  if(!desc.trim()){
    desc = scriptText.slice(0, 300);
  }
  return [`电影级真实主角半身定妆照，写实摄影质感，正脸微侧视线聚焦镜头，生动微表情，发丝真实光泽，自然皮肤肌理与毛孔质感，高级电影室内柔和光影，中性背景，8k超清。人设描述：${desc.trim().slice(0, 260)}`];
}

async function generateCastImagesForCurrentDrama(scriptText, requiredCount=1){
  const statusEl = $('#seq-director-status');
  const prompts = extractCharacterPrompts(scriptText, requiredCount);
  ensureR2VMode();

  window.labUserClearedRefs = false;
  if(typeof videoInputs !== 'undefined'){
    for(const item of [...(videoInputs.images || [])]){
      item.removed = true;
      item.controller?.abort();
    }
    videoInputs.images = [];
  }

  for(let idx = 0; idx < prompts.length; idx++){
    const charPrompt = prompts[idx];
    const tagLabel = `<Picture ${idx + 1}>`;
    if(statusEl) statusEl.innerHTML = `<span style="color:#2563eb;font-weight:600;">🎬 4090 正在渲染 ${tagLabel} 定妆照 (${idx + 1}/${prompts.length})…</span>`;
    const payload = {
      model: 'qi21',
      prompt: charPrompt,
      size: '1024x1024',
      steps: 20,
      cfg: 2.5,
      seed: '',
      request_id: crypto.randomUUID()
    };
    const initialJob = await api('/api/generation/lab/start', payload);
    const jobId = initialJob.id;
    let job = initialJob;
    const startTime = Date.now();
    for(let i=0; i<90; i++){
      await new Promise(r => setTimeout(r, 2000));
      const elapsed = Math.round((Date.now() - startTime) / 1000);
      if(statusEl) statusEl.innerHTML = `<span style="color:#d97706;font-weight:600;">🎬 4090 正在渲染 ${tagLabel} 定妆照 (${elapsed}秒)…</span>`;
      try {
        job = await api('/api/generation/job/' + jobId);
      } catch(netErr) {
        console.warn('轮询状态网络异常，继续重试:', netErr);
      }
      if(job.status === 'uncertain'){
        try {
          job = await api('/api/generation/job/' + jobId + '/reconcile', {});
        } catch(recErr) {
          console.warn('自动核实请求失败:', recErr);
        }
      }
      if(job.status === 'done' || job.status === 'error' || job.status === 'cancelled') break;
    }
    if(job.status !== 'done'){
      throw new Error(job.error || `${tagLabel} 定妆照未在预期时间内完成`);
    }
    await safeAttachVideoGalleryImage('images', job.id);
  }
  rememberLab();
  updateSequenceUI();
  refreshLab();
  if(statusEl){
    statusEl.innerHTML = `<span style="color:#16a34a;font-weight:600;">✅ 已完成 ${prompts.length} 张角色定妆照并锁定至 ${prompts.map((_, i) => '&lt;Picture ' + (i + 1) + '&gt;').join(' + ')}</span>`;
  }
}

function initAutoCastingControls(){
  const btn = $('#seq-auto-cast-btn');
  const statusEl = $('#seq-director-status');
  if(!btn) return;

  btn.onclick = async () => {
    let scriptText = '';
    if(typeof actsState !== 'undefined'){
      scriptText = actsState.map(a => a.prompt).filter(Boolean).join('\n');
    }
    if(!scriptText){
      scriptText = ($('#lab-prompt')?.value || '').trim();
    }
    if(!scriptText){
      toast('请先在上方 4 幕剧本中输入或选择一段剧情！');
      return;
    }

    if(lab.busy){
      const activeJob = lab.jobs.find(j => ['connecting','queued','running','downloading','reconnecting'].includes(j.status));
      const confirmed = confirm('4090 当前正有视频/媒体任务在渲染。\n是否先中止当前后台任务，以便立即生成角色定妆照？');
      if(!confirmed) return;
      if(activeJob){
        if(statusEl) statusEl.innerHTML = '<span style="color:#d97706;font-weight:600;">⏹️ 正在停止旧任务以释放 4090 算力…</span>';
        try{
          await api('/api/generation/job/' + activeJob.id + '/cancel', {});
          toast('已停止前序任务，4090 算力已释放');
          await refreshLab();
        }catch(e){
          console.warn('停止前序任务失败:', e);
        }
      }
    }

    const needsTwo = /<Picture\s*2>/i.test(scriptText) || (DRAMA_PRESETS[currentDramaPreset]?.characterPrompts?.length >= 2);
    const count = needsTwo ? 2 : 1;
    btn.disabled = true;
    toast(`🎬 导演模式已启动：正在根据当前剧情生成 ${count} 张角色定妆照…`);

    try {
      await generateCastImagesForCurrentDrama(scriptText, count);
      toast(`🎉 ${count} 张角色定妆照已出炉，并自动绑定至 ${needsTwo ? '<Picture 1> + <Picture 2>' : '<Picture 1>'}！`);
    } catch(err) {
      const isBusyErr = err.message && err.message.includes('已有媒体任务');
      if(isBusyErr){
        if(statusEl) statusEl.innerHTML = `<span style="color:#dc2626;">❌ 定妆受阻：4090 正在执行其他任务，请先点击右侧「⏹️ 停止生成」后再定妆</span>`;
        toast('定妆受阻：当前已有媒体任务在运行，请先点击右侧「⏹️ 停止生成」');
      } else {
        if(statusEl) statusEl.innerHTML = `<span style="color:#dc2626;">❌ 定妆失败：${escapeHTML(err.message)}</span>`;
        toast('自动定妆失败：' + err.message);
      }
    } finally {
      btn.disabled = false;
    }
  };

  const galleryBtn = $('#gallery-pick-btn');
  if(galleryBtn){
    galleryBtn.onclick = () => {
      ensureR2VMode();
      openVideoGallery('images');
    };
  }

  // 包装原生 pickVideoGalleryImage，确保选用图库图片时自动切为 r2v 并注入锁定描述
  const origPickVideoGalleryImage = window.pickVideoGalleryImage;
  if(typeof origPickVideoGalleryImage === 'function'){
    window.pickVideoGalleryImage = async function(jobId){
      ensureR2VMode();
      window.labUserClearedRefs = false;
      const res = await origPickVideoGalleryImage(jobId);
      let cur = $('#lab-prompt')?.value.trim() || '';
      const def = `subject_definitions:\n<Subject 1> is the primary protagonist whose facial identity, facial bone structure, eye appearance, authentic skin texture and natural hairstyle are preserved from <Picture 1>.`;
      if(cur && !cur.includes('subject_definitions:')){
        cur = def + '\n\n' + cur;
        $('#lab-prompt').value = cur;
      }
      rememberLab();
      updateSequenceUI();
      return res;
    };
  }

  const clearRefsBtn = $('#clear-all-refs-btn');
  if(clearRefsBtn){
    clearRefsBtn.onclick = () => clearAllVideoReferences();
  }
}

// ----------------------------------------------------
// 主循环与初始化
// ----------------------------------------------------
async function refreshLab(){
  clearTimeout(lab.timer);
  try{
    const data = await api('/api/generation/lab?kind=video&scope=' + $('#lab-scope').value + '&trash=' + (lab.trash ? '1' : '0'));
    lab.models = data.models;
    lab.jobs = data.jobs;
    lab.busy = data.busy;

    if(!lab.initialized){
      const savedDraft = readLabDraft();
      const draft = savedDraft || {
        ...(data.preferences['video'] || {}),
        workflow: 'r2v',
        video_checkpoint: 'hybrid',
        video_accelerator: 'none',
        video_ref_image_size: '1k',
        seq_seg_seconds: 5
      };
      $('#lab-model').innerHTML = data.models.filter(m => m.kind === 'video').map(m => `<option value="${escapeHTML(m.id)}">${escapeHTML(m.name)}</option>`).join('');
      if(data.models.some(m => m.id === draft.model && m.kind === 'video')) $('#lab-model').value = draft.model;

      // 如果用户曾经主动清空过参考图，不要从服务器偏好回填
      if(window.labUserClearedRefs && draft.references){
        draft.references = { images: [] };
      }

      configureLab(draft);
      if(!$('#lab-prompt').value.trim()) $('#lab-prompt').value = draft.prompt || SEQUENCE_TEMPLATES.seq_60s;
      if(!$('#lab-seed').value) $('#lab-seed').value = draft.seed ?? '';
      try{ lab.request = JSON.parse(localStorage.getItem(labStorage + '-submission') || 'null'); }catch{}
      lab.initialized = true;

      const query = new URLSearchParams(location.search), frameFrom = query.get('frame_from');
      if(frameFrom){
        query.delete('frame_from');
        history.replaceState(null, '', location.pathname + (query.toString() ? '?' + query.toString() : ''));
        try{ await setVideoFrameFromJob(frameFrom); toast('已用生成历史里的图片作为首帧'); }
        catch(e){ toast('无法使用该图片：' + e.message); }
      }
    }

    syncActsFromServerJobs(lab.jobs);
    renderLabHistory();
    if(lab.selected){
      const fresh = lab.jobs.find(j => j.id === lab.selected.id);
      if(fresh) lab.selected = fresh;
      renderLabJob(lab.selected);
    } else if(lab.jobs.length && !lab.trash){
      lab.selected = lab.jobs[0];
      renderLabJob(lab.selected);
    }

    // 若当前任务因网络抖动处于 uncertain（状态待核实），自动后台静默核实，无需用户手动点按或猜测
    if(lab.selected && lab.selected.status === 'uncertain'){
      const now = Date.now();
      if(!lab._lastAutoReconcile || (now - lab._lastAutoReconcile > 10000)){
        lab._lastAutoReconcile = now;
        api('/api/generation/job/' + lab.selected.id + '/reconcile', {}).catch(e => console.warn('后台自动核实中:', e));
      }
    }

    updateGenerateState();
  }catch(e){
    console.error('refreshLab error:', e);
  }finally{
    lab.timer = setTimeout(refreshLab, 3000);
  }
}

// ----------------------------------------------------
// 提交生成任务
// ----------------------------------------------------
$('#lab-form').onsubmit = async e => {
  e.preventDefault();
  if(($('#lab-model')?.value || 'h3') === 'h3'){
    return runAllActsSequence();
  }
  if(lab.submitting || lab.busy || lab.uploading) return;
  lab.submitting = true;
  updateGenerateState();
  $('#lab-submit-status').textContent = '正在准备生成参数…';

  try{
    const payload = labParameters();
    validateVideoParameters(payload);
    payload.request_id = crypto.randomUUID();
    lab.request = payload;
    try{ localStorage.setItem(labStorage + '-submission', JSON.stringify(lab.request)); }catch{}

    const job = await api('/api/generation/lab/start', payload);
    lab.request = null;
    localStorage.removeItem(labStorage + '-submission');
    renderLabJob(job);
    await refreshLab();

    const isSeq = payload.segments && payload.segments.length > 1;
    toast(isSeq ? '🚀 60s 连续微短剧任务已提交！ComfyUI 正在进行多段连续渲染与智能拼接！' : '视频任务已提交，4090 正在渲染中…');
    $('#lab-submit-status').textContent = '';
  }catch(err){
    $('#lab-submit-status').textContent = err.message;
    toast('提交失败：' + err.message);
  }finally{
    lab.submitting = false;
    updateGenerateState();
  }
};

$('#lab-model').onchange = () => { configureLab(currentLabValues()); rememberLab(); };
$('#media-size').onchange = () => { updateCustomSize('media'); rememberLab(); };
$('#lab-form').addEventListener('input', rememberLab);
$('#lab-form').addEventListener('change', rememberLab);
$('#lab-defaults').onclick = () => {
  configureLab();
  $('#lab-prompt').value = SEQUENCE_TEMPLATES.seq_60s;
  rememberLab();
  updateSequenceUI();
  toast('已恢复微短剧默认设置');
};
$('#lab-refresh').onclick = () => refreshLab();
$('#lab-scope').onchange = () => { lab.compare.clear(); renderLabCompare(); refreshLab(); };
$('#lab-history-search').oninput = renderLabHistory;
$('#lab-clear-compare').onclick = () => { lab.compare.clear(); lab.historySignature = null; renderLabHistory(); renderLabCompare(); };
$('#lab-refill').onclick = () => { if(lab.selected) fillLab(lab.selected); };
$('#lab-save').onclick = async () => {
  const j = lab.selected; if(!j || j.status !== 'done') return;
  const button = $('#lab-save'); button.disabled = true;
  try{
    if(j.saved?.new){ location.href = '/?article=' + encodeURIComponent(j.saved.new.slug); return; }
    const saved = await api('/api/generation/save/' + j.id, { mode: 'new' });
    j.saved = { ...(j.saved || {}), new: saved };
    toast('已保存为作品');
    if(lab.selected?.id === j.id) button.textContent = '打开已保存作品';
  }catch(err){
    toast(err.message);
  }finally{
    button.disabled = false;
  }
};
$('#lab-export').onclick = () => {
  if(!lab.selected) return;
  const blob = new Blob([JSON.stringify(jobExport(lab.selected), null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '测试参数-' + lab.selected.id.slice(0, 8) + '.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

// 页面装载入口
document.addEventListener('DOMContentLoaded', () => {
  initSequenceControls();
  initAutoCastingControls();
  enhanceReferenceRemoval();
  refreshLab();
});
