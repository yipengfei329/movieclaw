/**
 * 遗留模拟数据：仅订阅页（subscriptions-view）仍在使用。
 *
 * 发现页已切换为真实 TMDB 数据（见 lib/api/discover.ts），类型定义也已
 * 迁往 lib/media-types.ts；订阅功能接入后端后整个文件即可删除。
 */

import { cachedImageUrl } from "@/lib/image-proxy";
import type { MediaItem } from "@/lib/media-types";

const poster = (path: string) => cachedImageUrl(`https://image.tmdb.org/t/p/w500${path}`);
const backdrop = (path: string) => cachedImageUrl(`https://image.tmdb.org/t/p/w1280${path}`);

/* ---------------------------------- 电影 ---------------------------------- */

const movies: MediaItem[] = [
  {
    id: "dune2",
    type: "movie",
    title: "沙丘 2",
    originalTitle: "Dune: Part Two",
    year: 2024,
    rating: 8.3,
    genres: ["科幻", "冒险"],
    extent: "167 分钟",
    badges: ["4K", "HDR", "中字"],
    overview:
      "保罗·厄崔迪与弗雷曼人并肩作战，向摧毁他家族的阴谋者复仇。当宇宙的命运与挚爱之人只能二选一，他必须直面那场早已注定的恐怖未来。",
    posterUrl: poster("/8b8R8l88Qje9dn9OE8PY05Nxl1X.jpg"),
    backdropUrl: backdrop("/xOMo8BRK7PfcJv9JCnx7s5hj0PX.jpg"),
  },
  {
    id: "oppenheimer",
    type: "movie",
    title: "奥本海默",
    originalTitle: "Oppenheimer",
    year: 2023,
    rating: 8.9,
    genres: ["传记", "历史", "惊悚"],
    extent: "180 分钟",
    badges: ["4K", "HDR", "中字"],
    overview:
      "「原子弹之父」罗伯特·奥本海默的传奇与挣扎：他为世界带来终结战争的力量，也亲手打开了毁灭的潘多拉魔盒。",
    posterUrl: poster("/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg"),
    backdropUrl: backdrop("/rLb2cwF3Pazuxaj0sRXQ037tGI1.jpg"),
  },
  {
    id: "interstellar",
    type: "movie",
    title: "星际穿越",
    originalTitle: "Interstellar",
    year: 2014,
    rating: 9.4,
    genres: ["科幻", "冒险", "剧情"],
    extent: "169 分钟",
    badges: ["4K", "IMAX", "中字"],
    overview:
      "地球濒临毁灭，一队探险者穿越虫洞寻找人类的新家园。跨越星际的，不只是飞船，还有一位父亲对女儿的承诺。",
    posterUrl: poster("/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg"),
    backdropUrl: backdrop("/pbrkL804c8yAv3zBZR4QPEafpAR.jpg"),
  },
  {
    id: "inception",
    type: "movie",
    title: "盗梦空间",
    originalTitle: "Inception",
    year: 2010,
    rating: 9.4,
    genres: ["科幻", "悬疑", "动作"],
    extent: "148 分钟",
    badges: ["4K", "中字"],
    overview:
      "造梦师柯布带领团队潜入层层梦境，执行一次「往人心里种下想法」的不可能任务，而他自己的记忆才是最深的陷阱。",
    posterUrl: poster("/9gk7adHYeDvHkCSEqAvQNLV5Uge.jpg"),
  },
  {
    id: "darkknight",
    type: "movie",
    title: "蝙蝠侠：黑暗骑士",
    originalTitle: "The Dark Knight",
    year: 2008,
    rating: 9.2,
    genres: ["犯罪", "动作", "剧情"],
    extent: "152 分钟",
    badges: ["4K", "中字"],
    overview:
      "小丑将哥谭市拖入混乱的狂欢，蝙蝠侠被迫在英雄与罪人之间作出抉择——有些人只想看着世界燃烧。",
    posterUrl: poster("/qJ2tW6WMUDux911r6m7haRef0WH.jpg"),
  },
  {
    id: "parasite",
    type: "movie",
    title: "寄生虫",
    originalTitle: "기생충",
    year: 2019,
    rating: 8.8,
    genres: ["剧情", "惊悚", "喜剧"],
    extent: "132 分钟",
    badges: ["1080p", "中字"],
    overview:
      "底层一家四口逐一「寄生」进富豪家庭，一场大雨冲开了体面生活的地板，也冲出了阶层之间无法逾越的气味。",
    posterUrl: poster("/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg"),
  },
  {
    id: "eeaao",
    type: "movie",
    title: "瞬息全宇宙",
    originalTitle: "Everything Everywhere All at Once",
    year: 2022,
    rating: 7.6,
    genres: ["科幻", "喜剧", "动作"],
    extent: "139 分钟",
    badges: ["4K", "中字"],
    overview:
      "经营洗衣店的华裔主妇被卷入多元宇宙的疯狂冒险，在无数个「本可以成为的自己」之间，学会拥抱眼前的一地鸡毛。",
    posterUrl: poster("/w3LxiVYdWWRvEVdn5RYq6jIqkb1.jpg"),
  },
  {
    id: "spiderverse",
    type: "movie",
    title: "蜘蛛侠：纵横宇宙",
    originalTitle: "Spider-Man: Across the Spider-Verse",
    year: 2023,
    rating: 8.5,
    genres: ["动画", "科幻", "动作"],
    extent: "140 分钟",
    badges: ["4K", "HDR", "中字"],
    overview:
      "迈尔斯与格温穿梭于蜘蛛联盟守护的多元宇宙，当所有蜘蛛侠都告诉他「宿命不可违」，他决定改写自己的故事。",
    posterUrl: poster("/8Vt6mWEReuy4Of61Lnj5Xj704m8.jpg"),
  },
  {
    id: "thebatman",
    type: "movie",
    title: "新蝙蝠侠",
    originalTitle: "The Batman",
    year: 2022,
    rating: 7.4,
    genres: ["犯罪", "悬疑", "动作"],
    extent: "176 分钟",
    badges: ["4K", "HDR"],
    overview:
      "谜语人连环猎杀哥谭权贵，年轻的蝙蝠侠循着谜题深入城市腐败的根系，发现线索竟指向自己的家族。",
    posterUrl: poster("/74xTEgt7R36Fpooo50r9T25onhq.jpg"),
  },
  {
    id: "avatar2",
    type: "movie",
    title: "阿凡达：水之道",
    originalTitle: "Avatar: The Way of Water",
    year: 2022,
    rating: 7.8,
    genres: ["科幻", "冒险"],
    extent: "192 分钟",
    badges: ["4K", "3D", "中字"],
    overview:
      "杰克一家逃往潘多拉的海洋部落，学习「水之道」。当战火烧到珊瑚礁，他们必须为彼此而战。",
    posterUrl: poster("/t6HIqrRAclMCA60NsSmeqe9RmNV.jpg"),
    backdropUrl: backdrop("/s16H6tpK2utvwDtzZ8Qy4qm5Emw.jpg"),
  },
  {
    id: "topgun",
    type: "movie",
    title: "壮志凌云 2：独行侠",
    originalTitle: "Top Gun: Maverick",
    year: 2022,
    rating: 8.0,
    genres: ["动作", "剧情"],
    extent: "131 分钟",
    badges: ["4K", "IMAX"],
    overview:
      "服役三十余年的王牌飞行员「独行侠」重返 TOP GUN，训练一批年轻飞行员执行九死一生的任务，其中包括挚友之子。",
    posterUrl: poster("/62HCnUTziyWcpDaBO2i1DX17ljH.jpg"),
  },
  {
    id: "joker",
    type: "movie",
    title: "小丑",
    originalTitle: "Joker",
    year: 2019,
    rating: 8.7,
    genres: ["犯罪", "剧情"],
    extent: "122 分钟",
    badges: ["4K", "中字"],
    overview:
      "被社会反复碾过的喜剧演员亚瑟，在哥谭的冷漠里一步步走向疯狂——一个反派的诞生，也是一座城市的病历。",
    posterUrl: poster("/udDclJoHjfjb8Ekgsd4FDteOkCU.jpg"),
  },
  {
    id: "lalaland",
    type: "movie",
    title: "爱乐之城",
    originalTitle: "La La Land",
    year: 2016,
    rating: 8.4,
    genres: ["爱情", "歌舞"],
    extent: "128 分钟",
    badges: ["4K", "中字"],
    overview:
      "落魄爵士乐手与追梦女演员在洛杉矶相遇相爱。当梦想与爱情无法兼得，他们用一支想象中的舞，告别彼此。",
    posterUrl: poster("/uDO8zWDhfWwoFdKS4fzkUJt0Rf0.jpg"),
  },
  {
    id: "bladerunner",
    type: "movie",
    title: "银翼杀手 2049",
    originalTitle: "Blade Runner 2049",
    year: 2017,
    rating: 8.4,
    genres: ["科幻", "悬疑"],
    extent: "164 分钟",
    badges: ["4K", "HDR"],
    overview:
      "新一代银翼杀手 K 揭开一个足以颠覆社会秩序的秘密，并循线找到失踪三十年的前银翼杀手戴克。",
    posterUrl: poster("/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg"),
  },
  {
    id: "poorthings",
    type: "movie",
    title: "可怜的东西",
    originalTitle: "Poor Things",
    year: 2023,
    rating: 7.8,
    genres: ["奇幻", "喜剧", "剧情"],
    extent: "141 分钟",
    badges: ["4K", "中字"],
    overview:
      "被科学家复活的贝拉带着一颗崭新的心智周游世界，以孩童般的目光横冲直撞，长成完全属于自己的大人。",
    posterUrl: poster("/kCGlIMHnOm8JPXq3rXM6c5wMxcT.jpg"),
  },
  {
    id: "budapest",
    type: "movie",
    title: "布达佩斯大饭店",
    originalTitle: "The Grand Budapest Hotel",
    year: 2014,
    rating: 8.9,
    genres: ["喜剧", "剧情"],
    extent: "100 分钟",
    badges: ["1080p", "中字"],
    overview:
      "传奇礼宾员古斯塔沃与门童零，在名画失窃与家族阴谋间上演一场糖果色的亡命喜剧，献给逝去的旧欧洲。",
    posterUrl: poster("/eWdyYQreja6JGCzqHWXpWHDrrPo.jpg"),
  },
  {
    id: "whiplash",
    type: "movie",
    title: "爆裂鼓手",
    originalTitle: "Whiplash",
    year: 2014,
    rating: 8.7,
    genres: ["剧情", "音乐"],
    extent: "107 分钟",
    badges: ["1080p", "中字"],
    overview:
      "少年鼓手遇上以羞辱为教鞭的魔鬼导师，血与汗浸透鼓皮——伟大与毁灭，往往只隔一个节拍。",
    posterUrl: poster("/7fn624j5lj3xTme2SgiLCeuedmO.jpg"),
  },
  {
    id: "spiritedaway",
    type: "movie",
    title: "千与千寻",
    originalTitle: "千と千尋の神隠し",
    year: 2001,
    rating: 9.4,
    genres: ["动画", "奇幻"],
    extent: "125 分钟",
    badges: ["4K", "中字"],
    overview:
      "千寻误入神明的汤屋，为救变成猪的父母留下劳作。不要忘记自己的名字，就永远找得到回家的路。",
    posterUrl: poster("/39wmItIWsg5sZMyRUHLkWBcuVCM.jpg"),
  },
];

/* ---------------------------------- 剧集 ---------------------------------- */

const tvShows: MediaItem[] = [
  {
    id: "lastofus",
    type: "tv",
    title: "最后生还者",
    originalTitle: "The Last of Us",
    year: 2023,
    rating: 9.1,
    genres: ["剧情", "冒险", "末日"],
    extent: "共 2 季",
    badges: ["4K", "HDR", "中字"],
    overview:
      "真菌瘟疫毁灭文明二十年后，走私客乔尔护送可能拯救人类的少女艾莉横穿美国。末日里最危险的不是感染者，是人心，也是爱。",
    posterUrl: poster("/uKvVjHNqB5VmOrdxqAt2F7J78ED.jpg"),
    backdropUrl: backdrop("/uDgy6hyPd82kOHh6I95FLtLnj6p.jpg"),
  },
  {
    id: "arcane",
    type: "tv",
    title: "双城之战",
    originalTitle: "Arcane",
    year: 2021,
    rating: 9.3,
    genres: ["动画", "科幻", "剧情"],
    extent: "共 2 季",
    badges: ["4K", "HDR", "中字"],
    overview:
      "皮尔特沃夫的光鲜与祖安的阴沟之间，一对姐妹被时代的洪流推向对立两端。魔法科技点燃革命，也烧穿了亲情。",
    posterUrl: poster("/fqldf2t8ztc9aiwn3k6mlX3tvRT.jpg"),
    backdropUrl: backdrop("/q8eejQcg1bAqImEV8jh8RtBD4uH.jpg"),
  },
  {
    id: "breakingbad",
    type: "tv",
    title: "绝命毒师",
    originalTitle: "Breaking Bad",
    year: 2008,
    rating: 9.6,
    genres: ["犯罪", "剧情", "惊悚"],
    extent: "共 5 季",
    badges: ["4K", "中字"],
    overview:
      "身患绝症的化学老师沃尔特·怀特为家人铤而走险制毒，从懦弱的好人一步步蜕变为令人胆寒的「海森堡」。",
    posterUrl: poster("/ggFHVNu6YYI5L9pCfOacjizRGt.jpg"),
  },
  {
    id: "shogun",
    type: "tv",
    title: "幕府将军",
    originalTitle: "Shōgun",
    year: 2024,
    rating: 8.7,
    genres: ["历史", "剧情", "战争"],
    extent: "共 1 季",
    badges: ["4K", "HDR", "中字"],
    overview:
      "英国领航员漂流至战国末年的日本，被卷入吉井虎永与五大老的权力棋局。刀光与茶道之间，一子落错满盘皆输。",
    posterUrl: poster("/7O4iVfOMQmdCSxhOg1WnzG1AgYT.jpg"),
  },
  {
    id: "severance",
    type: "tv",
    title: "人生切割术",
    originalTitle: "Severance",
    year: 2022,
    rating: 9.1,
    genres: ["科幻", "悬疑", "惊悚"],
    extent: "共 2 季",
    badges: ["4K", "HDR", "中字"],
    overview:
      "卢蒙公司的员工接受「切割」手术，将工作与生活的记忆彻底分离。当「里面的我」开始追问自己是谁，整栋大楼开始崩塌。",
    posterUrl: poster("/lFf6LLrQjYldcZItzOkGmMMigP7.jpg"),
  },
  {
    id: "succession",
    type: "tv",
    title: "继承之战",
    originalTitle: "Succession",
    year: 2018,
    rating: 9.2,
    genres: ["剧情", "喜剧"],
    extent: "共 4 季",
    badges: ["1080p", "中字"],
    overview:
      "传媒帝国的年迈掌门迟迟不肯交棒，四个子女在饭桌与董事会之间互相撕咬——最贵的东西是爱，他们谁都买不起。",
    posterUrl: poster("/7HW47XbkNQ5fiwQFYGWdw9gs144.jpg"),
  },
  {
    id: "chernobyl",
    type: "tv",
    title: "切尔诺贝利",
    originalTitle: "Chernobyl",
    year: 2019,
    rating: 9.6,
    genres: ["历史", "剧情", "灾难"],
    extent: "迷你剧 · 5 集",
    badges: ["4K", "中字"],
    overview:
      "1986 年 4 号反应堆爆炸，一场看不见的灾难开始蔓延。谎言的代价并不是它会被当成真相，而是让我们再也认不出真相。",
    posterUrl: poster("/hlLXt2tOPT6RRnjiUmoxyG1LTFi.jpg"),
  },
  {
    id: "got",
    type: "tv",
    title: "权力的游戏",
    originalTitle: "Game of Thrones",
    year: 2011,
    rating: 9.4,
    genres: ["奇幻", "剧情", "战争"],
    extent: "共 8 季",
    badges: ["4K", "中字"],
    overview:
      "维斯特洛的七大王国为铁王座陷入血腥纷争，而在绝境长城之外，凛冬与死亡正一同逼近。",
    posterUrl: poster("/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg"),
  },
  {
    id: "bettercallsaul",
    type: "tv",
    title: "风骚律师",
    originalTitle: "Better Call Saul",
    year: 2015,
    rating: 9.5,
    genres: ["犯罪", "剧情"],
    extent: "共 6 季",
    badges: ["4K", "中字"],
    overview:
      "小律师吉米·麦吉尔如何一步步滑向「索尔·古德曼」：一场关于才华、亏欠与自我毁灭的漫长告别。",
    posterUrl: poster("/fC2HDm5t0kHl7mTm7jxMR31b7by.jpg"),
  },
  {
    id: "strangerthings",
    type: "tv",
    title: "怪奇物语",
    originalTitle: "Stranger Things",
    year: 2016,
    rating: 8.8,
    genres: ["科幻", "恐怖", "冒险"],
    extent: "共 5 季",
    badges: ["4K", "HDR", "中字"],
    overview:
      "印第安纳州小镇少年离奇失踪，随之浮出水面的，是秘密实验、超能力女孩与颠倒世界的入口。",
    posterUrl: poster("/49WJfeN0moxb9IPfGn8AIqMGskD.jpg"),
  },
  {
    id: "dark",
    type: "tv",
    title: "暗黑",
    originalTitle: "Dark",
    year: 2017,
    rating: 9.2,
    genres: ["科幻", "悬疑", "惊悚"],
    extent: "共 3 季",
    badges: ["4K", "中字"],
    overview:
      "德国小镇孩童接连失踪，牵出四个家族横跨三个时代的纠缠。问题不是凶手是谁，而是「何时」。",
    posterUrl: poster("/apbrbWs8M9lyOpJYU5WXrpFbk1Z.jpg"),
  },
  {
    id: "hotd",
    type: "tv",
    title: "龙之家族",
    originalTitle: "House of the Dragon",
    year: 2022,
    rating: 8.6,
    genres: ["奇幻", "剧情"],
    extent: "共 2 季",
    badges: ["4K", "HDR", "中字"],
    overview:
      "《权力的游戏》两百年前，坦格利安家族因王位继承分裂为黑绿两党，血龙狂舞的内战一触即发。",
    posterUrl: poster("/7QMsOTMUswlwxJP0rTTZfmz2tX2.jpg"),
  },
  {
    id: "truedetective",
    type: "tv",
    title: "真探",
    originalTitle: "True Detective",
    year: 2014,
    rating: 9.0,
    genres: ["犯罪", "悬疑", "剧情"],
    extent: "共 4 季",
    badges: ["1080p", "中字"],
    overview:
      "两名侦探跨越十七年追查路易斯安那的连环邪教凶案，时间是一个扁平的圆，罪与救赎在其中反复重演。",
    posterUrl: poster("/aowr4xpLP5sRCL50TkuADomJ98T.jpg"),
  },
  {
    id: "queensgambit",
    type: "tv",
    title: "后翼弃兵",
    originalTitle: "The Queen's Gambit",
    year: 2020,
    rating: 9.0,
    genres: ["剧情", "传记"],
    extent: "迷你剧 · 7 集",
    badges: ["4K", "中字"],
    overview:
      "孤儿院走出的象棋天才少女贝丝·哈蒙，一路碾压冷战时代的男性棋坛，同时与药瘾和孤独对弈。",
    posterUrl: poster("/zU0htwkhNvBQdVSIKB9s6hgVeFK.jpg"),
  },
  {
    id: "mandalorian",
    type: "tv",
    title: "曼达洛人",
    originalTitle: "The Mandalorian",
    year: 2019,
    rating: 8.7,
    genres: ["科幻", "冒险", "西部"],
    extent: "共 3 季",
    badges: ["4K", "HDR", "中字"],
    overview:
      "帝国覆灭后的银河边缘，孤独的曼达洛赏金猎人接下一单任务，目标却是一个绿色的小家伙。这是正道。",
    posterUrl: poster("/sWgBv7LV2PRoQgkxwlibdGXKz1S.jpg"),
  },
  {
    id: "thecrown",
    type: "tv",
    title: "王冠",
    originalTitle: "The Crown",
    year: 2016,
    rating: 8.8,
    genres: ["历史", "剧情", "传记"],
    extent: "共 6 季",
    badges: ["4K", "中字"],
    overview:
      "从加冕到世纪末，伊丽莎白二世在王冠与自我之间的漫长平衡术，一部英国王室的编年史诗。",
    posterUrl: poster("/1M876KPjulVwppEpldhdc8V4o68.jpg"),
  },
  {
    id: "wednesday",
    type: "tv",
    title: "星期三",
    originalTitle: "Wednesday",
    year: 2022,
    rating: 8.0,
    genres: ["喜剧", "奇幻", "悬疑"],
    extent: "共 2 季",
    badges: ["4K", "中字"],
    overview:
      "亚当斯家的大女儿星期三转学到奈弗莫学院，一边嫌弃所有人，一边冷面侦破缠绕学院的怪物连环案。",
    posterUrl: poster("/9PFonBhy4cQy7Jz20NpMygczOkv.jpg"),
  },
];

/* ---------------------------------- 订阅 ---------------------------------- */

/** 订阅状态：追更中（还有内容未入库）/ 已收齐 / 等待资源放出 */
export type SubscriptionState = "tracking" | "complete" | "waiting";

export const subscriptionStateMeta: Record<
  SubscriptionState,
  { label: string; color: string }
> = {
  tracking: { label: "追更中", color: "#6aa7ff" },
  complete: { label: "已收齐", color: "#4ade80" },
  waiting: { label: "待资源", color: "#f5c451" },
};

/** 一条本地订阅：影片本体 + 订阅状态 + 进度说明（如「更新至 S02E05」） */
export interface SubscriptionItem {
  media: MediaItem;
  state: SubscriptionState;
  note: string;
}

const byId = (list: MediaItem[]) => {
  const map = new Map(list.map((m) => [m.id, m]));
  return (ids: string[]) => ids.map((id) => map.get(id)!);
};

const pickMovies = byId(movies);
const pickTv = byId(tvShows);
const movie = (id: string) => pickMovies([id])[0];
const tv = (id: string) => pickTv([id])[0];

/**
 * 我的本地订阅（海报墙页数据）。
 * 排序即展示顺序：追更中在前（用户最关心）、待资源居中、已收齐殿后。
 */
export const subscriptions: SubscriptionItem[] = [
  { media: tv("lastofus"), state: "tracking", note: "更新至 S02E05" },
  { media: tv("severance"), state: "tracking", note: "更新至 S02E08" },
  { media: tv("strangerthings"), state: "tracking", note: "第 5 季更新至第 4 集" },
  { media: tv("wednesday"), state: "tracking", note: "第 2 季更新至第 6 集" },
  { media: tv("hotd"), state: "waiting", note: "第 3 季待播出" },
  { media: tv("truedetective"), state: "waiting", note: "第 5 季待资源放出" },
  { media: movie("dune2"), state: "complete", note: "4K HDR 已入库" },
  { media: movie("oppenheimer"), state: "complete", note: "4K REMUX 已入库" },
  { media: tv("shogun"), state: "complete", note: "全 10 集已收齐" },
  { media: tv("arcane"), state: "complete", note: "两季全集已收齐" },
  { media: movie("spiderverse"), state: "complete", note: "4K HDR 已入库" },
  { media: movie("poorthings"), state: "complete", note: "4K 中字已入库" },
];
