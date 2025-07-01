from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Node, Nodes, Plain, Image, Video
import httpx
import os
import hashlib
import aiofiles

from bs4 import BeautifulSoup
import traceback
import json
import unicodedata
import re
import glob

@register("astrbot_plugin_hyrz", "YourName", "astrbot_plugin_hyrz（火影忍者忍者信息查询）", "1.0.0")
class NinjaInfoPlugin(Star):
    def __init__(self, context: Context):
        self.context = context
        # 头像图片统一存放目录
        self.AVATAR_DIR = os.path.join(os.path.dirname(__file__), 'image')
        os.makedirs(self.AVATAR_DIR, exist_ok=True)

    async def initialize(self):
        logger.info("[hyrz_ninja_info] 插件初始化完成")

    def get_event_group_id(self, event):
        # 兼容多种获取方式
        if hasattr(event, "group_id"):
            return int(getattr(event, "group_id"))
        if hasattr(event, "message") and hasattr(event.message, "group_id"):
            return int(getattr(event.message, "group_id"))
        if hasattr(event, "get_group_id"):
            return int(event.get_group_id())
        return None

    async def send_group_forward_msg(self, api_url, group_id, forward_nodes, logger):
        url = f"{api_url}/send_group_forward_msg"
        data = {
            "group_id": group_id,
            "messages": forward_nodes
        }
        logger.info(f"[hyrz_ninja_info] Napcat合并转发API请求: {url} data={data}")
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=data)
            logger.info(f"[hyrz_ninja_info] Napcat合并转发API响应: {resp.text}")
            return resp.json()

    @filter.command("忍者信息")
    async def ninja_info(self, event: AstrMessageEvent):
        """查询火影忍者手游忍者信息（支持多个匹配，分批每批5个）"""
        message_str = event.message_str.strip()
        logger.info(f"[hyrz_ninja_info] 收到指令: {message_str}")
        parts = message_str.split(maxsplit=1)
        ninja_name = parts[1].replace(":", "").replace("：", "").strip() if len(parts) > 1 else ""
        logger.info(f"[hyrz_ninja_info] 解析到忍者名: {ninja_name}")
        if not ninja_name:
            logger.warning("[hyrz_ninja_info] 未输入忍者名")
            yield event.plain_result("请在指令后输入忍者名字，例如：/忍者信息 鸣人")
            return
        ninja_list = await self.get_ninja_ids(ninja_name)
        if not ninja_list:
            logger.warning(f"[hyrz_ninja_info] 未找到忍者：{ninja_name}")
            yield event.plain_result(f"未找到忍者：{ninja_name}")
            return
        BATCH_SIZE = 5
        total = len(ninja_list)
        batch_count = (total + BATCH_SIZE - 1) // BATCH_SIZE
        for batch_idx in range(batch_count):
            batch = ninja_list[batch_idx*BATCH_SIZE:(batch_idx+1)*BATCH_SIZE]
            nodes = Nodes([])
            if batch_count > 1:
                tip = f"共{total}个匹配，正在展示第{batch_idx+1}/{batch_count}批（每批5个）"
                nodes.nodes.append(Node(
                    uin=event.get_self_id(),
                    name="忍者情报官",
                    content=[Plain(tip)]
                ))
            for rzwyID, rzzmc, rzfmc in batch:
                info, avatar_url, _ = await self.get_ninja_info_with_avatar_and_ayvideo(rzzmc + rzfmc)
                show_name = rzzmc if not rzfmc else f"{rzzmc} {rzfmc}"
                avatar_path = None
                if avatar_url:
                    if avatar_url.startswith("//"):
                        avatar_url = "https:" + avatar_url
                    elif avatar_url.startswith("/"):
                        avatar_url = "https://hyrz.qq.com" + avatar_url
                    md5 = hashlib.md5(avatar_url.encode()).hexdigest()
                    avatar_path = os.path.join(self.AVATAR_DIR, f"avatar_{md5}.png")
                    if not os.path.exists(avatar_path):
                        os.makedirs(os.path.dirname(avatar_path), exist_ok=True)
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(avatar_url)
                            if resp.status_code == 200:
                                async with aiofiles.open(avatar_path, "wb") as f:
                                    await f.write(resp.content)
                node_content = []
                if avatar_path and os.path.exists(avatar_path):
                    node_content.append(Image.fromFileSystem(avatar_path))
                node_content.append(Plain(f"{show_name}"))
                if info:
                    node_content.append(Plain(info.strip()))
                nodes.nodes.append(Node(
                    uin=event.get_self_id(),
                    name="忍者情报官",
                    content=node_content
                ))
            yield event.chain_result([nodes])

    async def get_ninja_ids(self, ninja_name: str):
        """
        返回所有模糊匹配的忍者ID和名字，格式[(rzwyID, rzzmc, rzfmc)]
        """
        url = 'https://hyrz.qq.com/act/a20221108gjx/ninja-data/ninja_list.json'
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                "Referer": "https://hyrz.qq.com/cp/a20230308ren/index.shtml"
            }
            logger.info(f"[hyrz_ninja_info] 请求忍者列表: {url}")
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10)
            text = response.text.strip()
            if text.startswith("getNinjaList("):
                text = text[len("getNinjaList("):-1]
            data = json.loads(text)
            key = self.normalize_name(ninja_name)
            result = []
            for ninja in data.get('list', []):
                rzzmc = self.normalize_name(ninja.get('rzzmc', ''))
                rzfmc = self.normalize_name(ninja.get('rzfmc', ''))
                full_name = rzzmc + rzfmc
                # 只要主名、副名、组合任意包含关键字都算
                if key in rzzmc or key in rzfmc or key in full_name:
                    result.append((str(ninja.get('rzwyID')), ninja.get('rzzmc', ''), ninja.get('rzfmc', '')))
            return result
        except Exception as e:
            logger.error(f"忍者列表查找ID出错: {e}\n{traceback.format_exc()}")
            return []

    def normalize_name(self, s):
        # 去除空格、全角/半角符号，只保留中文、英文、数字
        s = unicodedata.normalize('NFKC', s)
        s = re.sub(r'[「」\[\]（）()\s·,，。、《》:：]', '', s)
        return s.lower()

    async def get_ninja_id(self, ninja_name: str) -> str:
        url = 'https://hyrz.qq.com/act/a20221108gjx/ninja-data/ninja_list.json'
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                "Referer": "https://hyrz.qq.com/cp/a20230308ren/index.shtml"
            }
            logger.info(f"[hyrz_ninja_info] 请求忍者列表: {url}")
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10)
            text = response.text.strip()
            if text.startswith("getNinjaList("):
                text = text[len("getNinjaList("):-1]
            data = json.loads(text)
            key = self.normalize_name(ninja_name)
            # 先主名模糊匹配
            candidates = []
            for ninja in data.get('list', []):
                rzzmc = self.normalize_name(ninja.get('rzzmc', ''))
                rzfmc = self.normalize_name(ninja.get('rzfmc', ''))
                full_name = rzzmc + rzfmc
                if key in rzzmc or key in rzfmc:
                    candidates.append(ninja)
            # 如果唯一命中
            if len(candidates) == 1:
                logger.info(f"[hyrz_ninja_info] 主名/副名唯一模糊命中: {candidates[0].get('rzzmc')} {candidates[0].get('rzfmc')} ID={candidates[0].get('rzwyID')}")
                return str(candidates[0].get('rzwyID'))
            # 多个同名，尝试主名+副名组合
            for ninja in data.get('list', []):
                rzzmc = self.normalize_name(ninja.get('rzzmc', ''))
                rzfmc = self.normalize_name(ninja.get('rzfmc', ''))
                full_name = rzzmc + rzfmc
                if key in full_name:
                    logger.info(f"[hyrz_ninja_info] 主名+副名组合命中: {ninja.get('rzzmc')} {ninja.get('rzfmc')} ID={ninja.get('rzwyID')}")
                    return str(ninja.get('rzwyID'))
            logger.warning(f"[hyrz_ninja_info] 未找到忍者：{ninja_name}")
            return None
        except Exception as e:
            logger.error(f"忍者列表查找ID出错: {e}\n{traceback.format_exc()}")
            return None

    async def get_ninja_detail(self, rzwyID: str) -> str:
        url = f'https://hyrz.qq.com/act/a20221108gjx/ninja-data/{rzwyID}.json'
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                "Referer": "https://hyrz.qq.com/cp/a20230308ren/index.shtml"
            }
            logger.info(f"[hyrz_ninja_info] 请求详细数据: {url}")
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10)
            logger.info(f"[hyrz_ninja_info] 详细数据HTTP状态: {response.status_code}")
            text = response.text.strip()
            if text.startswith("getNinjaData("):
                text = text[len("getNinjaData("):-1]
            data = json.loads(text)
            # 提取主要信息
            ninja_name = data['zhanshi']['rzzs']['rzzmc']
            ninja_title = data['zhanshi']['rzzs']['rzfmc']
            skill_desc = data['zhanshi']['rzzs']['rztc']
            # 推荐通灵
            tjtls = []
            for k in ['tjtls1', 'tjtls2']:
                v = data.get('tjtlmj', {}).get(k, {})
                if v and v.get('tjtlsmc'+k[-1], ''):
                    tjtls.append(v['tjtlsmc'+k[-1]])
            # 推荐秘卷
            tjmj = []
            for k in ['tjmj1', 'tjmj2']:
                v = data.get('tjtlmj', {}).get(k, {})
                if v and v.get('tjmjmc'+k[-1], ''):
                    tjmj.append(v['tjmjmc'+k[-1]])

            # 快速上手部分
            # 普攻（平A）
            pgjn = data.get('jnzs', {}).get('pg', {})
            pg_list = []
            for i in range(1, 6):
                name = pgjn.get(f'pg{i}mc', '')
                desc = pgjn.get(f'pg{i}ms', '')
                if name:
                    pg_list.append(f"{name}: {desc}")
            # 一技能
            yjn = data.get('jnzs', {}).get('yjn', {})
            yjn_list = []
            for i in range(1, 6):
                name = yjn.get(f'yjn{i}mc', '')
                desc = yjn.get(f'yjn{i}ms', '')
                if name:
                    yjn_list.append(f"{name}: {desc}")
            # 二技能
            ejn = data.get('jnzs', {}).get('ejn', {})
            ejn_list = []
            for i in range(1, 6):
                name = ejn.get(f'ejn{i}mc', '')
                desc = ejn.get(f'ejn{i}ms', '')
                if name:
                    ejn_list.append(f"{name}: {desc}")
            # 奥义
            ay = data.get('jnzs', {}).get('ay', {})
            ay_list = []
            for i in range(1, 6):
                name = ay.get(f'ay{i}mc', '')
                desc = ay.get(f'ay{i}ms', '')
                if name:
                    ay_list.append(f"{name}: {desc}")
            # 特殊机制
            tsjz = data.get('jnzs', {}).get('tsjz', {})
            tsjz_list = []
            for i in range(1, 6):
                name = tsjz.get(f'tsjz{i}mc', '')
                desc = tsjz.get(f'tsjz{i}ms', '')
                if name:
                    tsjz_list.append(f"{name}: {desc}")

            # 整合输出
            result = f"忍者：{ninja_name}「{ninja_title}"
            if pg_list:
                result += f"\n【普攻】\n" + "\n".join(pg_list)
            if yjn_list:
                result += f"\n【一技能】\n" + "\n".join(yjn_list)
            if ejn_list:
                result += f"\n【二技能】\n" + "\n".join(ejn_list)
            if ay_list:
                result += f"\n【奥义】\n" + "\n".join(ay_list)
            if tsjz_list:
                result += f"\n【特殊机制】\n" + "\n".join(tsjz_list)
            result += f"\n技能说明：{skill_desc}\n推荐通灵：{'、'.join(tjtls) if tjtls else '无'}\n推荐秘卷：{'、'.join(tjmj) if tjmj else '无'}"
            logger.info(f"[hyrz_ninja_info] 详细数据整合完成: {ninja_name}")
            return result
        except Exception as e:
            logger.error(f"详细页爬取忍者信息出错: {e}\n{traceback.format_exc()}")
            return "查询忍者信息时发生错误，请稍后再试。"

    # 新增：带头像url和奥义视频url的详细信息获取
    async def get_ninja_info_with_avatar_and_ayvideo(self, ninja_name: str):
        rzwyID = await self.get_ninja_id(ninja_name)
        if not rzwyID:
            return None, None, None
        url = f'https://hyrz.qq.com/act/a20221108gjx/ninja-data/{rzwyID}.json'
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
                "Referer": "https://hyrz.qq.com/cp/a20230308ren/index.shtml"
            }
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=10)
            text = response.text.strip()
            if text.startswith("getNinjaData("):
                text = text[len("getNinjaData("):-1]
            data = json.loads(text)
            avatar_url = data['zhanshi']['rzzs'].get('lbtx', None)
            ay_video_url = data.get('lztj-ay', {}).get('ayjs', {}).get('aytsp', None)
            # 复用原有详细信息整合逻辑
            ninja_name = data['zhanshi']['rzzs']['rzzmc']
            ninja_title = data['zhanshi']['rzzs']['rzfmc']
            skill_desc = data['zhanshi']['rzzs']['rztc']
            tjtls = []
            for k in ['tjtls1', 'tjtls2']:
                v = data.get('tjtlmj', {}).get(k, {})
                if v and v.get('tjtlsmc'+k[-1], ''):
                    tjtls.append(v['tjtlsmc'+k[-1]])
            tjmj = []
            for k in ['tjmj1', 'tjmj2']:
                v = data.get('tjtlmj', {}).get(k, {})
                if v and v.get('tjmjmc'+k[-1], ''):
                    tjmj.append(v['tjmjmc'+k[-1]])
            pgjn = data.get('jnzs', {}).get('pg', {})
            pg_list = []
            for i in range(1, 6):
                name = pgjn.get(f'pg{i}mc', '')
                desc = pgjn.get(f'pg{i}ms', '')
                if name:
                    pg_list.append(f"{name}: {desc}")
            yjn = data.get('jnzs', {}).get('yjn', {})
            yjn_list = []
            for i in range(1, 6):
                name = yjn.get(f'yjn{i}mc', '')
                desc = yjn.get(f'yjn{i}ms', '')
                if name:
                    yjn_list.append(f"{name}: {desc}")
            ejn = data.get('jnzs', {}).get('ejn', {})
            ejn_list = []
            for i in range(1, 6):
                name = ejn.get(f'ejn{i}mc', '')
                desc = ejn.get(f'ejn{i}ms', '')
                if name:
                    ejn_list.append(f"{name}: {desc}")
            ay = data.get('jnzs', {}).get('ay', {})
            ay_list = []
            for i in range(1, 6):
                name = ay.get(f'ay{i}mc', '')
                desc = ay.get(f'ay{i}ms', '')
                if name:
                    ay_list.append(f"{name}: {desc}")
            tsjz = data.get('jnzs', {}).get('tsjz', {})
            tsjz_list = []
            for i in range(1, 6):
                name = tsjz.get(f'tsjz{i}mc', '')
                desc = tsjz.get(f'tsjz{i}ms', '')
                if name:
                    tsjz_list.append(f"{name}: {desc}")
            result = f"忍者：{ninja_name}「{ninja_title}"
            if pg_list:
                result += f"\n【普攻】\n" + "\n".join(pg_list)
            if yjn_list:
                result += f"\n【一技能】\n" + "\n".join(yjn_list)
            if ejn_list:
                result += f"\n【二技能】\n" + "\n".join(ejn_list)
            if ay_list:
                result += f"\n【奥义】\n" + "\n".join(ay_list)
            if tsjz_list:
                result += f"\n【特殊机制】\n" + "\n".join(tsjz_list)
            result += f"\n技能说明：{skill_desc}\n推荐通灵：{'、'.join(tjtls) if tjtls else '无'}\n推荐秘卷：{'、'.join(tjmj) if tjmj else '无'}"
            return result, avatar_url, ay_video_url
        except Exception as e:
            logger.error(f"详细页爬取忍者信息出错: {e}\n{traceback.format_exc()}")
            return None, None, None

    @filter.command("删除头像缓存")
    async def delete_ninja_cache(self, event: AstrMessageEvent):
        """删除image文件夹下所有头像缓存"""
        deleted = 0
        for file in glob.glob(os.path.join(self.AVATAR_DIR, '*')):
            try:
                os.remove(file)
                deleted += 1
            except Exception as e:
                pass
        yield event.plain_result(f"已删除{deleted}个头像缓存文件。")

    async def terminate(self):
        logger.info("[hyrz_ninja_info] 插件已卸载") 
