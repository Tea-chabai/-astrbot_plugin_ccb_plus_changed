# -- coding: utf-8 --
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from collections import deque
from astrbot.api import AstrBotConfig

import time
import json
import random
import os
from .back import time_long, volume
DATA_FILE = "data/ccb.json"

LOG_FILE = "data/ccb_log.json"

HELP_INFO = """
/ccb ccb，顾名思义，用来ccb 用法： ccb [@]，如果不带有@某人则根据配置文件进行自交或者打胶
/ccbinfo  查询某人ccb信息：第一次对他ccb的人，被ccb的总次数，注入总量，用法：ccbinfo [@目标]
/ccbtop 按次数排行
/ccbmax 按max值排行并输出产生者
/ccbvol 按注入量排行
/xnn XNN榜 计算群中最xnn特质的群友
/打胶 没有什么特别的，独立出来的打胶功能
/ccbclear   管理员指令：清除某人的所有 CCB 记录，用法：ccbclear [@目标]
/ccbnodo  管理员指令：切换目标防被 CCB 状态，用法：ccbnodo [@目标]

根据配置文件可调控炸膛的概率

享受赛博打胶与ccb吧🦌🦌🦌
"""

a1 = "id"       # qq号
a2 = "num"      # 北朝次数
a3 = "vol"      # 被注入量
a4 = "ccb_by"   # 被谁朝了
a5 = "max"      # 最大值
#a6 = "luguan"   #撸管量
#a7 = "lu_num"   #撸管次数


def get_avatar(user_id: str) -> bytes:
    return f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"

def makeit(group_data, target_user_id):
    return 1 if any(item.get(a1) == target_user_id for item in group_data) else 2

@register("ccb", "Koikokokokoro", "和群友赛博sex的插件PLUS", "1.1.4")
class ccb(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.window = config.get("yw_window")                 # 滑动窗口长度（秒）
        self.threshold = config.get("yw_threshold")               # 窗口内最大允许动作次数
        self.ban_duration = config.get("yw_ban_duration")         # 禁用时长（秒）
        self.faint_duration = config.get("faint_ban_duration")    #晕倒时长（秒）
        self.faint_random_min = config.get("faint_random_min")    #晕倒随机最小时长（秒）
        self.faint_random_max = config.get("faint_random_max")    #晕倒随机最大时长（秒）
        self.action_times = {}
        self.ban_list = {}
        self.faint_list = {}
        self.yw_prob = config.get("yw_probability")               # 触发概率
        self.white_list  = config.get("white_list")
        self.selfdo = self.config.get("self_ccb", False)         # 0721 默认为否
        self.crit_prob = self.config.get("crit_prob")         #暴击概率
        self.faint_prob = self.config.get("faint_prob")          #晕倒概率
        self.is_log = self.config.get("is_log", False)           # 完整日志，默认为false
        
    #  from issue 6
    async def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    def _save_white_list(self):
        try:
            self.config["white_list"] = self.white_list
            save_fn = getattr(self.config, "save", None)
            if callable(save_fn):
                save_fn()
        except Exception as e:
            logger.warning(f"保存白名单失败: {e}")

    async def _get_nickname(self, event: AstrMessageEvent, user_id: str, strict_event: bool = False) -> str:
        nickname = user_id
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                if strict_event:
                    assert isinstance(event, AiocqhttpMessageEvent)
                stranger_info = await event.bot.api.call_action(
                    'get_stranger_info', user_id=user_id
                )
                nickname = stranger_info.get("nick", nickname)
            except Exception:
                pass
        return nickname

    # 获取目标用户ID
    def _get_target_user_id(self, event: AstrMessageEvent) -> str:
        self_id = str(event.get_self_id())
        return next(
            (str(seg.qq) for seg in event.get_messages()
             if isinstance(seg, Comp.At) and str(seg.qq) != self_id),
            str(event.get_sender_id())
        )

    # 重新计算由于clear导致的缺口
    def _recalc_max(self, record: dict):
        if not isinstance(record, dict):
            return
        ccb_by = record.get(a4, {}) or {}
        total_num = 0
        try:
            total_num = int(record.get(a2, 0))
        except Exception:
            total_num = 0
        try:
            total_vol = float(record.get(a3, 0))
        except Exception:
            total_vol = 0.0
#        try:
#            total_num2 = int(record.get(a2, 0))
#            except Exception:
#            total_num2 = 0
#        try:
#            total_vol2 = float(record.get(a3, 0))
#        except Exception:
#            total_vol2 = 0.0
        if total_num <= 0 or not ccb_by:
            record[a5] = 0.0
            for k, v in ccb_by.items():
                if isinstance(v, dict):
                    v["max"] = False
            record[a4] = ccb_by
            return
        record[a5] = round(total_vol / total_num, 2)
        try:
            best_id = max(
                ccb_by.items(),
                key=lambda x: int(x[1].get("count", 0)) if isinstance(x[1], dict) else 0
            )[0]
        except Exception:
            best_id = None
        if best_id:
            for k, v in ccb_by.items():
                if isinstance(v, dict):
                    v["max"] = (k == best_id)
        record[a4] = ccb_by

    def read_data(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"读取数据错误: {e}")
        return {}

    def write_data(self, data):
        try:
            with open(DATA_FILE, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"写入数据错误: {e}")

    # 记录日志
    def append_log(self, group_id: str, executor_id: str, target_id: str, time: float, vol: float):
        """
        记录日志，格式为：
        {"executor": "...", ````````}
        """
        try:
            # 读取日志，可能用于数据处理
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, 'r', encoding='utf-8') as lf:
                    try:
                        logs = json.load(lf)
                        if not isinstance(logs, list):
                            logs = []
                    except Exception:
                        logs = []
            else:
                logs = []

            # 追加日志内容
            entry = {
                "group": group_id,
                "executor": executor_id,
                "target": target_id,
                "time": time,
                "vol": str(round(float(vol), 2))
            }
            logs.append(entry)

            # 写回
            with open(LOG_FILE, 'w', encoding='utf-8') as lf:
                json.dump(logs, lf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"append_log 失败: {e}")


    @filter.command("ccbhelp")
    async def get_help(self, event: AstrMessageEvent):
        """
        显示帮助信息
        """
        yield event.plain_result(HELP_INFO)


    @filter.command("ccb")
    async def ccb(self, event: AstrMessageEvent):
        """
        ccb，顾名思义，用来ccb
        用法： ccb [@]
        """

        group_id = str(event.get_group_id())
        send_id = str(event.get_sender_id())
        actor_id = send_id
        faint_min = self.faint_random_min
        faint_max = self.faint_random_max
        now = time.time()
        f_now = time.time()
        if self.faint_duration <= 0:
            faint_time = self.faint_duration
        else:
            faint_time = round(random.uniform(faint_min, faint_max), 2)

        yw_prob_r1 = random.random()
        if yw_prob_r1 < self.yw_prob:
            yw_prob_r = yw_prob_r1
        else:
            faint_prob_r = random.random()


        # 检查是否在禁用期内
        ban_end = self.ban_list.get(actor_id, 0)
        faint_end = self.faint_list.get(actor_id, 0)
        
        if now < ban_end:
            remain = int(ban_end - now)
            m, s = divmod(remain, 60)
            yield event.plain_result(f"嘻嘻，你已经一滴不剩了，电子阳痿中，剩余 {m}分{s}秒")
            return
        if f_now < faint_end:
            remain = int(faint_end - f_now)
            m1, s1 = divmod(remain, 60)
            yield event.plain_result(f"{user_name} 正在昏厥中，剩余 {m1}分{s1}秒，现在的ta毫无还手之力")
            return

        # 窗口时间统计
        times = self.action_times.setdefault(actor_id, deque())
        while times and now - times[0] > self.window:
            times.popleft()
        times.append(now)

        # 超阈值禁用
        if len(times) > self.threshold:
            self.ban_list[actor_id] = now + self.ban_duration
            times.clear()
            yield event.plain_result("你现在已经一滴不剩了，进入贤者模式")
            return

        target_user_id = self._get_target_user_id(event)

        if target_user_id in self.white_list:
            stranger_info = await event.bot.api.call_action(
                'get_stranger_info', user_id=target_user_id
            )
            nickname = stranger_info.get("nick", target_user_id)
            yield event.plain_result(f"{nickname} 的洞洞被掌握CCB的神封印了，不能被C力（悲")
            return

        if target_user_id == actor_id and not self.selfdo:
            if len(times) > self.threshold:
                self.ban_list[actor_id] = now + self.ban_duration
                times.clear()
                yield event.plain_result("你现在已经一滴不剩了，再冲就是雪了（悲")
            else:
                timep = round(random.uniform(1, 600), 2)
                V = round(random.uniform(0.001,100), 3)
                a = time_long(timep)
                b = volume(V)
                user_name = event.get_sender_name()
                yield event.plain_result(f"Hello, {user_name}, 你坚持了{timep}s哦，{a}.射出{V}ml,{b}!") 
                if yw_prob_r < self.yw_prob:
                    self.ban_list[actor_id] = now + self.ban_duration
                    yield event.plain_result("💥你炸膛了，萎")
                if faint_prob_r < self.faint_prob:
                    self.faint_list[actor_id] = f_now + faint_time
                    yield event.plain_result(f"{user_name} 不小心晕了,接下来ta将毫无还手之力")
            
            return




        # CCB 逻辑
        duration = round(random.uniform(1, 60), 2)
        V = round(random.uniform(0.001, 100), 3)
        prob = self.crit_prob
        crit = False
        user_name = event.get_sender_name()
#        faint = False
        is_log = self.is_log
        if random.random() < prob:
            V = round(V * 2, 2)
            crit = True

        pic = get_avatar(target_user_id)

        all_data = self.read_data()
        group_data = all_data.get(group_id, [])

        mode = makeit(group_data, target_user_id)
        if mode == 1:
            # 已有记录，更新
            try:
                for item in group_data:
                    if item.get(a1) == target_user_id:
                        # 获取昵称
                        nickname = await self._get_nickname(event, target_user_id, strict_event=True)

                        # 更新 num / vol / ccb_by
                        item[a2] = int(item.get(a2, 0)) + 1
                        item[a3] = round(float(item.get(a3, 0)) + V, 2)

                        # 添加逻辑：记录max值的产生者
                        ccb_by = item.get(a4, {}) or {}
                        if send_id in ccb_by:
                            ccb_by[send_id]["count"] = ccb_by[send_id].get("count", 0) + 1
                            ccb_by[send_id]["first"] = ccb_by[send_id].get("first", False)
                        else:
                            ccb_by[send_id] = {"count": 1, "first": False, "max": False}

                        # 添加逻辑：记录max值

                        # 计算max
                        raw_prev = item.get(a5, None)
                        prev_max = 0.00
                        if raw_prev is not None:
                            try:
                                prev_max = float(raw_prev)
                            except (TypeError, ValueError):
                                prev_max = 0.00
                        # 如果不存在合法的 max，使用平均值
                        if prev_max == 0.00:
                            try:
                                total_vol = float(item.get(a3, 0))
                                total_num = int(item.get(a2, 0))
                                if total_num > 0:
                                    prev_max = round(total_vol / total_num, 2)
                                else:
                                    prev_max = 0.00
                            except Exception:
                                prev_max = 0.00

                        if float(V) > prev_max:
                            item[a5] = round(float(V), 2)
                            for k in ccb_by:
                                ccb_by[k]["max"] = False
                            ccb_by[send_id]["max"] = True
                        else:
                            for k in ccb_by:
                                if "max" not in ccb_by[k]:
                                    ccb_by[k]["max"] = False

                        item[a4] = ccb_by

                        if crit:
                            chain = [
                                Comp.Plain(f"{user_name} 和 {nickname} 发生了{duration}min长的ccb行为，{nickname}被注入了{V:.2f}ml的生命因子"),
                                Comp.Image.fromURL(pic),
                                Comp.Plain(f"这是ta的第{item[a2]}次。ta被累积注入了{item[a3]}ml的生命因子。")
                            ]
                        else:
                            # 发送结果
                            chain = [
                                Comp.Plain(f"{user_name} 和 {nickname} 发生了{duration}min长的ccb行为，{nickname}被注入了{V:.2f}ml的生命因子"),
                                Comp.Image.fromURL(pic),
                                Comp.Plain(f"这是ta的第{item[a2]}次。ta被累积注入了{item[a3]}ml的生命因子。")
                            ]
                        yield event.chain_result(chain)

                        # 是否保留完整日志
                        if is_log:
                            try:
                                self.append_log(group_id, send_id, target_user_id, duration, V)
                            except Exception as e:
                                logger.warning(f"记录日志失败: {e}")

                        # 写回数据
                        all_data[group_id] = group_data
                        self.write_data(all_data)

                        # 随机养胃
                        if yw_prob_r < self.yw_prob:
                            self.ban_list[actor_id] = now + self.ban_duration
                            yield event.plain_result("💥你在这轮后因为用力过猛被迫进入了贤者模式（悲")
                            
                    
                        #随机昏厥
                        if faint_prob_r < self.faint_prob and target_user_id == actor_id:
                            self.faint_list[target_user_id] = f_now + faint_time
                            yield event.plain_result(f"{nickname}被自己弄晕了,接下来ta将毫无还手之力")
                        if faint_prob_r < self.faint_prob and target_user_id != actor_id:
                            self.faint_list[target_user_id] = f_now + faint_time
                            yield event.plain_result(f"{nickname} 被 {user_name} C晕了,接下来ta将毫无还手之力")
                            
                        return
            except Exception as e:
                logger.error(f"报错: {e}")
                yield event.plain_result("对方拒绝了和你ccb")
                return

        else:
            # 新记录
            try:
                nickname = await self._get_nickname(event, target_user_id, strict_event=True)

                chain = [
                    Comp.Plain(f"{user_name} 和 {nickname}发生了{duration}min长的ccb行为，{nickname}被注入了{V:.2f}ml的生命因子"),
                    Comp.Image.fromURL(pic),
                    Comp.Plain("这是ta的初体验~，你把人家的处给破了喵～要负责哦喵～")
                ]
                yield event.chain_result(chain)

                # 构造并保存新记录
                new_record = {
                    a1: target_user_id,
                    a2: 1,
                    a3: round(V, 3),
                    a4: {send_id: {"count": 1, "first": True, "max": True}},
                    a5: round(V, 3)
                }
                group_data.append(new_record)
                all_data[group_id] = group_data
                self.write_data(all_data)

                # 是否保留完整日志
                if is_log:
                    try:
                        self.append_log(group_id, send_id, target_user_id, duration, V)
                    except Exception as e:
                        logger.warning(f"记录日志失败: {e}")

                # 随机养胃
                if yw_prob_r < self.yw_prob:
                    self.ban_list[actor_id] = now + self.ban_duration
                    yield event.plain_result("💥你在这轮后因为用力过猛被迫进入了贤者模式（悲")
                    

                #随机昏厥
                if faint_prob_r < self.faint_prob and actor_id == target_user_id:
                    self.faint_list[target_user_id] = f_now + faint_time
                    yield event.plain_result(f"{nickname}被自己弄晕了,接下来ta将毫无还手之力")
                    


                if faint_prob_r < self.faint_prob and actor_id != target_user_id:
                    self.faint_list[target_user_id] = f_now + faint_time
                    yield event.plain_result(f"{nickname} 被 {user_name}C晕了,接下来ta将毫无还手之力")
                    

                return
            except Exception as e:
                logger.error(f"报错: {e}")
                yield event.plain_result("对方拒绝了和你ccb")
                return

    @filter.command("ccbtop")
    async def ccbtop(self, event: AstrMessageEvent):
        """
        按次数排行
        """
        group_id = str(event.get_group_id())
        group_data = self.read_data().get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录。")
            return

        top5 = sorted(group_data, key=lambda x: int(x.get(a2, 0)), reverse=True)[:5]
        msg = "被ccb排行榜 TOP5：\n"
        for i, r in enumerate(top5, 1):
            uid = r[a1]
            nick = await self._get_nickname(event, uid)
            msg += f"{i}. {nick} - 次数：{r[a2]}\n"
        yield event.plain_result(msg)

    @filter.command("ccbvol")
    async def ccbvol(self, event: AstrMessageEvent):
        """
        按注入量排行
        """
        group_id = str(event.get_group_id())
        group_data = self.read_data().get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录。")
            return

        top5 = sorted(group_data, key=lambda x: float(x.get(a3, 0)), reverse=True)[:5]
        msg = "被注入量排行榜 TOP5：\n"
        for i, r in enumerate(top5, 1):
            uid = r[a1]
            nick = await self._get_nickname(event, uid)
            msg += f"{i}. {nick} - 共被注入：{float(r[a3]):.2f}ml\n"
        yield event.plain_result(msg)

    @filter.command("ccbinfo")
    async def ccbinfo(self, event: AstrMessageEvent):
        """
        查询某人ccb信息：第一次对他ccb的人，被ccb的总次数，注入总量
        用法：ccbinfo [@目标]
        """
        group_id = str(event.get_group_id())
        target_user_id = self._get_target_user_id(event)

        # 读取群数据
        all_data = self.read_data()
        group_data = all_data.get(group_id, [])

        # 查找目标记录
        record = next((r for r in group_data if r.get(a1) == target_user_id), None)
        if not record:
            yield event.plain_result("该用户暂无ccb记录。")
            return

        # 总次数 & 总注入量
        total_num = int(record.get(a2, 0))
        total_vol = float(record.get(a3, 0))

        raw_max = record.get(a5, None)
        max_val = 0.0
        try:
            if raw_max is not None:
                max_val = float(raw_max)
            else:
                if total_num > 0:
                    max_val = round(total_vol / total_num, 2)
        except Exception:
            max_val = 0.0

        # 计算ccb次数
        cb_total = 0
        try:
            for rec in group_data:
                by = rec.get(a4, {}) or {}
                info = by.get(target_user_id)
                if info:
                    cb_total += int(info.get("count", 0))
        except Exception:
            cb_total = 0

        # 找出第一次的操作者
        ccb_by = record.get(a4, {})
        first_actor = None
        for actor_id, info in ccb_by.items():
            if info.get("first"):
                first_actor = actor_id
                break

        # 如果没标记 first，就选 count 最大的作为“首位”
        if not first_actor and ccb_by:
            first_actor = max(ccb_by.items(), key=lambda x: x[1].get("count", 0))[0]

        # 获取昵称
        first_nick = first_actor or "未知"
        if first_actor:
            first_nick = await self._get_nickname(event, first_actor, strict_event=True)

        # 输出结果
        msg = (
            f"【{record.get(a1)} 】\n"
            f"• 破壁人：{first_nick}\n"
            f"• 被超：{total_num}\n"
            f"• ccb：{cb_total}\n"
            f"• 被注入：{total_vol:.2f}ml\n"
            f"• MAX：{max_val:.2f}ml"
#            f"• 撸出：{total_vol2:.2f}ml\n"
        )
        yield event.plain_result(msg)

    # 单次注入排行榜
    @filter.command("ccbmax")
    async def ccbmax(self, event: AstrMessageEvent):
        """
        按max值排行并输出产生者
        """
        group_id = str(event.get_group_id())
        group_data = self.read_data().get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录。")
            return

        # 计算max
        entries = []
        for r in group_data:
            raw_max = r.get(a5, None)
            max_val = 0.0
            try:
                if raw_max is not None:
                    max_val = float(raw_max)
                else:
                    total_vol = float(r.get(a3, 0))
                    total_num = int(r.get(a2, 0))
                    if total_num > 0:
                        max_val = round(total_vol / total_num, 2)
            except Exception:
                max_val = 0.0
            entries.append((r, float(max_val)))

        # 排序
        entries.sort(key=lambda x: x[1], reverse=True)
        top5 = entries[:5]

        msg = "单次最大注入排行榜 TOP5：\n"
        for i, (r, max_val) in enumerate(top5, 1):
            uid = r.get(a1)
            # 解析产生者
            producer_id = None
            ccb_by = r.get(a4, {}) or {}
            for actor_id, info in ccb_by.items():
                if info.get("max"):
                    producer_id = actor_id
                    break
            # 若没有显式标记，则回退选取count最大者
            if not producer_id and ccb_by:
                try:
                    producer_id = max(ccb_by.items(), key=lambda x: x[1].get("count", 0))[0]
                except Exception:
                    producer_id = None

            # 获取昵称
            nick = await self._get_nickname(event, uid, strict_event=True)
            producer_nick = producer_id or "未知"
            if producer_id:
                producer_nick = await self._get_nickname(event, producer_id, strict_event=True)

            msg += f"{i}. {nick} - MAX注入：{max_val:.2f}ml（{producer_nick}）\n"

        yield event.plain_result(msg)

    @filter.command("xnn")
    async def xnn(self, event: AstrMessageEvent):
        """
        XNN榜
        计算群中最xnn特质的群友
        """
        # 配置权重
        w_num = 1.0
        w_vol = 0.1
        w_action = 0.5

        group_id = str(event.get_group_id())
        all_data = self.read_data()
        group_data = all_data.get(group_id, [])
        if not group_data:
            yield event.plain_result("当前群暂无ccb记录。")
            return

        # 统计每个人对别人的操作次数
        actor_actions = {}
        for record in group_data:
            ccb_by = record.get(a4, {})
            for actor_id, info in ccb_by.items():
                actor_actions[actor_id] = actor_actions.get(actor_id, 0) + info.get("count", 0)

        # 计算xnn值
        ranking = []
        for record in group_data:
            uid = record.get(a1)
            num = int(record.get(a2, 0))
            vol = float(record.get(a3, 0))
            actions = actor_actions.get(uid, 0)
            xnn_value = num * w_num + vol * w_vol - actions * w_action
            ranking.append((uid, xnn_value))

        # 排序
        ranking.sort(key=lambda x: x[1], reverse=True)
        top5 = ranking[:5]

        # 构造输出
        msg = "💎 小南梁 TOP5 💎\n"
        for idx, (uid, xnn_val) in enumerate(ranking[:5], 1):
            nick = await self._get_nickname(event, uid, strict_event=True)
            msg += (
                f"{idx}. {nick} - XNN值：{xnn_val:.2f} \n"
                f"(被ccb次数：{num}，容量：{vol:.2f}ml，对他人ccb：{actions})\n"
            )

        yield event.plain_result(msg)

    # issue 6
    @filter.command("ccbclear")
    async def ccbclear(self, event: AstrMessageEvent):
        """
        管理员指令：清除某人的所有 CCB 记录
        用法：ccbclear [@目标]
        """
        group_id = str(event.get_group_id())
        if not await self._is_admin(event):
            yield event.plain_result("无权限使用此命令")
            return

        target_user_id = self._get_target_user_id(event)

        all_data = self.read_data()
        group_data = all_data.get(group_id, [])
        if not isinstance(group_data, list):
            group_data = []

        before_len = len(group_data)
        group_data = [r for r in group_data if isinstance(r, dict) and r.get(a1) != target_user_id]
        removed_self = before_len - len(group_data)

        removed_from_others = 0
        modified_records = []
        for record in group_data:
            if not isinstance(record, dict):
                continue
            ccb_by = record.get(a4, {}) or {}
            if target_user_id in ccb_by:
                try:
                    removed_from_others += int(ccb_by[target_user_id].get("count", 0))
                except Exception:
                    removed_from_others += 0
                del ccb_by[target_user_id]
                record[a4] = ccb_by
                record[a2] = sum(
                    int(info.get("count", 0)) for info in ccb_by.values() if isinstance(info, dict)
                )
                modified_records.append(record)

        for record in modified_records:
            self._recalc_max(record)

        all_data[group_id] = group_data
        self.write_data(all_data)

        msg = (
            f"已清除 {target_user_id} 的 CCB 记录：\n"
            f"删除自身被CCB记录：{removed_self} 条\n"
            f"移除ccb他人记录：{removed_from_others} 次\n"
            f"相关记录已重新校准"
        )
        yield event.plain_result(msg)

    @filter.command("ccbnodo")
    async def ccbnodo(self, event: AstrMessageEvent):
        """
        管理员指令：切换目标防被 CCB 状态
        用法：ccbnodo [@目标]
        """
        if not await self._is_admin(event):
            yield event.plain_result("无权限使用此命令")
            return

        target_user_id = self._get_target_user_id(event)
        if target_user_id in self.white_list:
            self.white_list = [uid for uid in self.white_list if uid != target_user_id]
            self._save_white_list()
            yield event.plain_result(f"已解除 {target_user_id} 的防被C保护")
        else:
            self.white_list.append(target_user_id)
            self._save_white_list()
            yield event.plain_result(f"已将 {target_user_id} 加入防被C保护名单")
    
    @filter.command("打胶")
    async def dajiao(self, event: AstrMessageEvent):
        """
        就是打胶，没有特别的（会炸膛哦，笑
        """
        faint_min = self.faint_random_min
        faint_max = self.faint_random_max
        timep = round(random.uniform(1, 666), 2)
        V = round(random.uniform(0.01,114), 2)
        a = time_long(timep)
        b = volume(V)
        user_name = event.get_sender_name()
        send_id = str(event.get_sender_id())
        actor_id = send_id
        now = time.time()
        f_now = time.time()
        ban_end = self.ban_list.get(actor_id, 0)
        faint_end = self.faint_list.get(actor_id, 0)
        yw_prob_r1 = random.random()
        if yw_prob_r1 < self.yw_prob:
            yw_prob_r = yw_prob_r1
        else:
            faint_prob_r = random.random()

        if self.faint_duration <= 0:
            faint_time = self.faint_duration
        else:
            faint_time = round(random.uniform(faint_min, faint_max), 2)
        
        
        
        if now < ban_end:
            remain = int(ban_end - now)
            m, s = divmod(remain, 60)
            yield event.plain_result(f"嘻嘻，你已经一滴不剩了，电子阳痿中，剩余 {m}分{s}秒")
            return
        if f_now < faint_end:
            remain = int(faint_end - f_now)
            m1, s1 = divmod(remain, 60)
            yield event.plain_result(f"{user_name} 正在昏厥中，剩余 {m1}分{s1}秒，现在的ta毫无还手之力")
            
            return
        yield event.plain_result(f"Hello, {user_name}, 你坚持了{timep}s哦，{a}.射出{V}ml,{b}!") 
        if yw_prob_r < self.yw_prob:
            self.ban_list[actor_id] = now + self.ban_duration
            yield event.plain_result("💥你炸膛了，进入贤者模式")
        
        if faint_prob_r < self.faint_prob:
            self.faint_list[actor_id] = f_now + faint_time
            yield event.plain_result(f"{user_name}不小心鹿晕了,接下来ta将毫无还手之力")

        