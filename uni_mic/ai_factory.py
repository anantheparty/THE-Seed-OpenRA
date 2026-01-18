from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Dict, Any, Optional
from openai import OpenAI
import re
import OpenRA_Copilot_Library as OpenRA
from OpenRA_Copilot_Library import *
from OpenRA_Copilot_Library import TargetsQueryParam
import os
from .config import ConfigManager,base_path
from concurrent.futures import ThreadPoolExecutor
from .log_manager import LogManager
from .prompt_manager import PromptManager
from .prompt_context import PromptContext, PlanItem
import time
import yaml
import traceback
import threading
from .utils import parse_response, CODE_REGEX, SPEECH_REGEX, TITLE_REGEX, MEMORY_REGEX
from .error_handler import ErrorHandler, ErrorHandlerManager
from .ui_manager import UIManager
import random
import io
import sys
import contextlib

# 全局配置
MAX_OUTPUT_TOKENS = 3000

logger = LogManager.get_logger()
api = GameAPI(host="localhost")
game_api = api

class BaseAIAssistant(ABC):
    def __init__(self, ui_manager: Optional[UIManager] = None):
        self.config = ConfigManager.get_config().starter
        self.ui_manager = ui_manager
        self.client = self._create_client()
        self.prompt_manager = PromptManager()
        self.context = PromptContext()
        self.current_input = ""
        self._init_context()
        self._load_noise_keywords()
        self.api = OpenRA.GameAPI("localhost")
        self.executor = ThreadPoolExecutor(max_workers=10)

    def _init_context(self):
        # 初始化配置数据
        self._load_game_config()
        self._load_api_prompt()
        self._load_sample_code()
        self.context.game_state.start_time = time.perf_counter()

    def _load_game_config(self):
        config_path = os.path.join(base_path, 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            self.context.config_data = yaml.safe_load(f)

    def _load_api_prompt(self):
        api_prompt_path = os.path.join(base_path, 'OpenRA_Promt.py')
        with open(api_prompt_path, 'r', encoding='utf-8') as f:
            self.context.api_prompt_content = f.read()

    def _load_sample_code(self):
        if not self.config.no_sample:
            if self.config.single_sample:
                sample_path = os.path.join(base_path, 'Sample.py')
                with open(sample_path, 'r', encoding='utf-8') as f:
                    self.context.sample_code = f.read()
            else:
                # 加载多个样例代码的逻辑
                sample_dir = os.path.join(base_path, 'samples')
                self.context.sample_code = ""
                if os.path.exists(sample_dir):
                    for sample_file in os.listdir(sample_dir):
                        if sample_file.endswith('.py'):
                            sample_path = os.path.join(sample_dir, sample_file)
                            with open(sample_path, 'r', encoding='utf-8') as f:
                                self.context.sample_code += f"# {sample_file}\n"
                                self.context.sample_code += f.read() + "\n\n"
                pass

    def update_game_state(self, api):
        """更新游戏状态"""
        player_info = api.player_base_info_query()
        self.context.game_state.cash = player_info.Cash
        self.context.game_state.resources = player_info.Resources
        self.context.game_state.power = player_info.Power
        
        visible_units = api.query_actor(
            TargetsQueryParam(
            )
        )
        self.context.game_state.visible_units = [
            {
                "actor_id": unit.actor_id,
                "faction": unit.faction,
                "type": unit.type,
                "position": {"x": unit.position.x, "y": unit.position.y}
            }
            for unit in visible_units
            if unit.faction != "中立"
        ]

    def update_memory(self, memory: str):
        """更新记忆"""
        self.context.game_state.memory = memory

    def _load_noise_keywords(self):
        noise_path = os.path.join(base_path, 'noise_keywords.yaml')
        with open(noise_path, 'r', encoding='utf-8') as f:
            self.noise_keywords = yaml.safe_load(f)['noise_keywords']
        logger.info(f"加载的噪声关键词: {self.noise_keywords}")  

    @abstractmethod
    def _create_client(self):
        pass

    @abstractmethod
    def generate_response(self) -> str:
        pass
    
    def handle_strategy_command(self, user_input=None, ui_manager: Optional[UIManager] = None):
        """处理策略命令,通过UIManager与GUI交互"""
        manager = ui_manager or self.ui_manager
        plan_status_label = None

        try:
            # 更新计划状态到上下文
            if manager:
                try:
                    manager.post_player_dialog(user_input)
                    all_plans = manager.get_all_plans()
                    self.context.game_state.plans = [
                        PlanItem(
                            name=plan['name'],
                            status=plan['status'],
                            timestamp=plan['timestamp']
                        )
                        for plan in all_plans
                    ]
                except Exception as e:
                    logger.error(f"Failed to update plans via UIManager: {e}")

            # 检查语音底噪
            if user_input and any(keyword in user_input for keyword in self.noise_keywords):
                logger.warning(f"检测到语音底噪，忽略指令: {user_input}")
                return

            # 设置当前输入
            self.current_input = user_input if user_input else ""
            
            # 生成响应
            logger.info("Generating AI response...")
            response = self.generate_response()
            logger.info("AI response received.")

            # 解析响应内容
            code_match = CODE_REGEX.search(response)
            speech_match = SPEECH_REGEX.search(response)
            title_match = TITLE_REGEX.search(response)
            memory_match = MEMORY_REGEX.search(response)

            plan_name_for_lookup = None

            # 更新GUI显示 (使用UIManager)
            if manager:
                if speech_match:
                    manager.post_ai_dialog(speech_match.group(1).strip())
                    logger.info(f"AI Speech: {speech_match.group(1).strip()}")
                if title_match:
                    plan_name_for_lookup = title_match.group(1).strip()
                    manager.post_plan_item(plan_name=plan_name_for_lookup, status="进行中")
                    logger.info(f"AI Add Title: {plan_name_for_lookup}")
                if memory_match:
                    mem_content = memory_match.group(1).strip()
                    manager.post_set_memory_content(mem_content)
                    self.update_memory(mem_content)
                    logger.info(f"AI Update Memory: {mem_content}")
            # 执行代码
            if code_match:
                executable = code_match.group(1).strip()
                logger.info(f"准备执行代码:\n{executable}")

                # 直接异步调用run_code_and_update_plan
                future = self.executor.submit(self.run_code_and_update_plan, executable, plan_name_for_lookup, manager)
                logger.info("Code execution submitted.")

            elif plan_name_for_lookup and manager:
                def update_no_code_plan_status(name, status_to_set, mgr):
                    time.sleep(0.5)
                    label_to_update = None
                    try:
                        label_to_update = mgr.gui.plan_items.get(name, {}).get('label')
                    except Exception as find_e:
                        logger.error(f"Error finding plan label for no-code update '{name}': {find_e}")
                    
                    if label_to_update:
                        logger.info(f"Plan '{name}' added with no code. Marking as '{status_to_set}'.")
                        mgr.post_update_plan_status(label_to_update, status_to_set)
                    else:
                        logger.warning(f"Plan '{name}' added with no code, but label not found for status update.")

                final_status = "已完成" if speech_match else "未开始"
                threading.Thread(target=update_no_code_plan_status, args=(plan_name_for_lookup, final_status, manager), daemon=True).start()

        except Exception as e:
            logger.error(f"处理策略命令时发生顶层错误: {str(e)}")
            logger.error(f"错误堆栈: {traceback.format_exc()}")
            if manager:
                manager.post_ai_dialog(f"处理命令时出错: {str(e)}", False)

    @abstractmethod
    def handle_error_with_llm(self, error_info: Dict[Any, Any], prev_response_id: str = None) -> tuple[bool, Optional[str]]:
        """
        处理错误的基础方法
        返回值: (success, response_id)
        """
        pass

    def _get_error_prompt(self, error_info: Dict[Any, Any]) -> str:
        """生成错误处理的提示"""
        return f"""请分析以下代码执行错误并提供解决方案：
错误信息：{error_info['error']}
执行的命令：{error_info['command']}
原始代码：
{error_info['code']}

请分析错误原因并生成修复后的代码。如果无法修复，请回复"没有解决方案"。
"""

    def _execute_error_fix(self, response: str, ui_manager: Optional[UIManager] = None, error_info: Dict[Any, Any] = None) -> bool:
        """同步执行修复代码，并自动处理plan状态，返回是否修复成功"""
        try:
            code_match = CODE_REGEX.search(response)
            speech_match = SPEECH_REGEX.search(response)
            title_match = TITLE_REGEX.search(response)

            plan_name_for_lookup = None

            # 更新GUI显示
            if ui_manager:
                if speech_match:
                    ui_manager.post_ai_dialog(speech_match.group(1).strip())
                    logger.info(f"AI Speech: {speech_match.group(1).strip()}")
                if title_match:
                    plan_name_for_lookup = title_match.group(1).strip()
                    ui_manager.post_plan_item(plan_name=plan_name_for_lookup, status="进行中")
                    logger.info(f"AI Add Title: {plan_name_for_lookup}")

            # 执行修复代码
            if code_match:
                executable = code_match.group(1).strip()
                logger.info(f"准备执行修复代码:\n{executable}")
                # 直接同步调用run_code_and_update_plan
                # 直接调用有可能上面的post_plan_item没准备好,sleep 100ms
                time.sleep(0.1)
                success = self.run_code_and_update_plan(executable, plan_name_for_lookup, ui_manager, need_handle_error=False)
                return success
            else:
                logger.warning("修复响应中未找到可执行代码")
                return False
        except Exception as e:
            logger.error(f"处理修复响应时出错: {str(e)}")
            if ui_manager:
                ui_manager.post_ai_dialog(f"处理修复响应时出错: {str(e)}", False)
            return False

    def run_code_and_update_plan(self, code_to_run, plan_name=None, local_ui_manager=None, need_handle_error:bool=True):
        """
        执行代码，并自动更新plan状态
        """
        target_label = None
        if local_ui_manager and plan_name:
            try:
                target_label = local_ui_manager.gui.plan_items.get(plan_name, {}).get('label')
                if not target_label:
                    logger.warning(f"未找到计划 {plan_name} 的状态label，后续无法自动更新计划状态。")
            except Exception as find_e:
                logger.error(f"查找计划label时出错: {find_e}")

        try:
            logger.info(f"Executing code in thread: {threading.current_thread().name}")
            exec(code_to_run)
            logger.info("Code execution successful.")
            if local_ui_manager and target_label:
                local_ui_manager.post_update_plan_status(target_label, "已完成")
                logger.info(f"AI Update Plan Status: {plan_name} -> 已完成")
            return True
        except Exception as exec_e:
            logger.error(f"执行代码出错: {str(exec_e)}")
            logger.error(f"错误堆栈: {traceback.format_exc()}")
            if local_ui_manager:
                error_message_short = "".join(traceback.format_exception_only(type(exec_e), exec_e)).strip()
                if target_label:
                    local_ui_manager.post_update_plan_status(target_label, "失败")
                    logger.info(f"AI Update Plan Status: {plan_name} -> 失败")
                local_ui_manager.post_ai_dialog(f"错误信息：{error_message_short}", False)
                if need_handle_error:
                    error_info = {
                        'timestamp': time.perf_counter() - self.context.game_state.start_time,
                        'command': self.current_input,
                        'error': error_message_short,
                        'code': code_to_run
                    }
                    self.context.add_error(error_info)
                    logger.info("Initiating error handling...")
                    initial_response_id = getattr(self, 'last_response_id', None)
                    try:
                        error_handler = ErrorHandlerManager().create_handler(self)
                        error_handler.handle_error(error_info, local_ui_manager, initial_response_id)
                    except Exception as eh_e:
                        logger.error(f"Failed to initiate error handler: {eh_e}")
            return False

class OpenAIAssistant(BaseAIAssistant):
    def __init__(self, ui_manager: Optional[UIManager] = None):
        super().__init__(ui_manager=ui_manager)

    def _create_client(self):
        logger.info("Initializing OpenAI client")
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def generate_response(self) -> str:
        self.update_game_state(self.api)
        static_prompt, dynamic_prompt = self.prompt_manager.get_prompts(self.context)
        messages = [
            {"role": "system", "content": static_prompt + dynamic_prompt},
            {"role": "user", "content": self.current_input}
        ]
        
        try:
            completion = self.client.chat.completions.create(
                model=self.config.gptmodel,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=1.0
            )
            logger.info(f"OpenAI API response received: {completion}")
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI API error: {str(e)}")
            raise

    def handle_error_with_llm(self, error_info: Dict[Any, Any], prev_response_id: str = None) -> tuple[bool, Optional[str]]:
        messages = [
            {"role": "system", "content": self.prompt_manager._generate_error_prompt(error_info, self.context)},
            {"role": "user", "content": "请提供修复方案"}
        ]
        try:
            completion = self.client.chat.completions.create(
                model=self.config.gptmodel,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.7
            )
            response = completion.choices[0].message.content
            if response and "没有解决方案" not in response:
                success = self._execute_error_fix(response, self.ui_manager, error_info)
                return success, None
            else:
                return False, None
        except Exception as e:
            logger.error(f"Error handling failed: {str(e)}")
            return False, None

class DeepseekAIAssistant(OpenAIAssistant):
    def __init__(self, ui_manager: Optional[UIManager] = None):
        super().__init__(ui_manager=ui_manager)
        logger.info("Initializing Deepseek client")
        self.client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )

    def _create_client(self):
        return self.client

class OpenAIResponseAIAssistant(BaseAIAssistant):
    def __init__(self, ui_manager: Optional[UIManager] = None):
        super().__init__(ui_manager=ui_manager)
        self.last_response_id: Optional[str] = None
        self.remenber_cnt = 3

    def _create_client(self):
        logger.info("Initializing OpenAI Response client")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise AIResponseError(
                "NO_API_KEY",
                "OPENAI_API_KEY 未设置",
                {"hint": "设置环境变量 OPENAI_API_KEY"}
            )
        return OpenAI(api_key=api_key)

    # —— 内部工具：尽量把各种可能的文本拿全，避免 index 固定取值 ——
    def _extract_text(self, response: Any) -> str:
        """
        兼容 Responses API 的不同分支：
        - response.output_text（SDK 便捷字段，若有）
        - response.output[*].content[*].text
        - （必要时可扩展：message/choices 等其它分支）
        """
        chunks: List[str] = []

        # 1) 有些 SDK 版本带有便捷聚合文本
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            chunks.append(output_text.strip())

        # 2) 遍历标准 output 列表
        output = getattr(response, "output", None)
        if isinstance(output, list):
            for out in output:
                parts = getattr(out, "content", None)
                if isinstance(parts, list):
                    for p in parts:
                        # dict 或 pydantic-like 对象都兼容
                        text = None
                        if isinstance(p, dict):
                            text = p.get("text")
                        else:
                            text = getattr(p, "text", None)
                        if isinstance(text, str) and text.strip():
                            chunks.append(text.strip())

        # 3) 兜底：如果真的什么都没拿到，抛出带结构摘要的错误
        if not chunks:
            summary = self._summarize_response_shape(response)
            raise AIResponseError(
                "NO_TEXT_FOUND",
                "响应中未找到可用的文本内容",
                {"shape": summary}
            )

        # 合并（保留换行，避免割裂）
        return "\n".join(chunks).strip()

    def _summarize_response_shape(self, response: Any) -> dict:
        """
        打印/记录结构摘要，方便你在日志里快速判断到底返回了什么。
        """
        def safe(obj, name, default=None):
            try:
                return getattr(obj, name, default)
            except Exception:
                return default

        summary = {
            "id": safe(response, "id"),
            "model": safe(response, "model"),
            "output_len": len(safe(response, "output", []) or []),
            "has_output_text": bool(safe(response, "output_text")),
            "output_types": [],
            "content_types": [],
        }
        out = safe(response, "output", None)
        if isinstance(out, list):
            for o in out:
                summary["output_types"].append(safe(o, "type"))
                parts = safe(o, "content", [])
                if isinstance(parts, list):
                    summary["content_types"].append([getattr(p, "type", p.get("type", None)) if isinstance(p, dict) else getattr(p, "type", None) for p in parts])
        return summary

    def generate_response(self) -> str:
        # （可选）你的项目逻辑
        self.update_game_state(self.api)
        static_prompt, dynamic_prompt = self.prompt_manager.get_prompts(self.context)

        if self.config.use_simplest_prompt and self.last_response_id:
            instructions = self.prompt_manager.get_simplest_prompt() + dynamic_prompt
        else:
            instructions = static_prompt + dynamic_prompt

        try:
            response = self.client.responses.create(
                model=self.config.gptmodel,
                input=self.current_input,                 # 你原逻辑保留
                instructions=instructions,                # 指令拼接
                previous_response_id=self.last_response_id,  # 线程化
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=1.0,
            )
            self.last_response_id = getattr(response, "id", self.last_response_id)

            text = self._extract_text(response)
            logger.info("OpenAI Response API OK | id=%s | text_len=%d", self.last_response_id, len(text))
            return text

        # —— 细分 OpenAI 典型异常 —— 
        except AuthenticationError as e:
            raise AIResponseError("AUTH_ERROR", "鉴权失败，请检查 API Key", {"error": str(e)}) from e
        except RateLimitError as e:
            raise AIResponseError("RATE_LIMIT", "达到速率/配额限制", {"error": str(e)}) from e
        except APIConnectionError as e:
            raise AIResponseError("NETWORK_ERROR", "与 OpenAI 通信失败", {"error": str(e)}) from e
        except BadRequestError as e:
            raise AIResponseError("BAD_REQUEST", "请求参数有误", {"error": str(e)}) from e
        except APIError as e:
            # 服务端 5xx 或未细分的 API 异常
            raise AIResponseError("API_ERROR", "OpenAI 服务端错误", {"error": str(e)}) from e
        except AIResponseError:
            # 我们主动抛出的结构化错误，直接透传
            raise
        except Exception as e:
            # 兜底：不丢失信息
            raise AIResponseError("UNEXPECTED_ERROR", "未知异常", {"error": repr(e)}) from e

    def handle_error_with_llm(self, error_info: Dict[Any, Any], prev_response_id: str = None) -> tuple[bool, Optional[str]]:
        try:
            response = self.client.responses.create(
                model=self.config.gptmodel,
                input="请提供修复方案",
                instructions=self.prompt_manager._generate_error_prompt(error_info, self.context),
                previous_response_id=prev_response_id,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.7
            )
            logger.info(f"OpenAI Response API response received(In Error Handling): {response}")
            response_text = response.output[0].content[0].text
            if response_text and "没有解决方案" not in response_text:
                success = self._execute_error_fix(response_text, self.ui_manager, error_info)
                return success, response.id
            else:
                return False, response.id
        except Exception as e:
            logger.error(f"Error handling failed: {str(e)}")
            return False, None

class OpenAIRealtimeAIAssistant(BaseAIAssistant):
    def __init__(self, ui_manager: Optional[UIManager] = None):
        super().__init__(ui_manager=ui_manager)
        self.last_response_id = None
        self.ws_client = None
    
    def _create_client(self):
        logger.info("Initializing OpenAI Realtime client")
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
    def generate_response(self) -> str:
        self.update_game_state(self.api)
        static_prompt, dynamic_prompt = self.prompt_manager.get_prompts(self.context)
        
        messages = [
            {"role": "system", "content": "你是一个OpenRA游戏的指令提取助手。请从玩家的自然语言中提取出关键的游戏指令，或者对于游戏的指挥，命令等行为，复述即可，忽略对话内容，。如果没有识别到有效指令，输出 没有指令"},
            {"role": "user", "content": self.current_input}
        ]
        
        try:
            completion = self.client.chat.completions.create(
                model=self.config.gptmodel_pre,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.7
            )
            extracted_command = completion.choices[0].message.content
            logger.info(f"OpenAI Realtime API response received: {extracted_command}")
            
            if "没有" in extracted_command:
                self.current_input = ""
                return ""
            
            self.current_input = extracted_command.strip()
            
            static_prompt, dynamic_prompt = self.prompt_manager.get_prompts(self.context)
            
            if self.config.use_simplest_prompt and self.last_response_id:
                instructions = self.prompt_manager.get_simplest_prompt() + dynamic_prompt
            else:
                instructions = static_prompt + dynamic_prompt
                
            response = self.client.responses.create(
                model=self.config.gptmodel,
                input=self.current_input,
                instructions=instructions,
                previous_response_id=self.last_response_id,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=1.0
            )
            
            self.last_response_id = response.id
            logger.info(f"OpenAI Response API response received: {response}")
            return response.output[0].content[0].text
            
        except Exception as e:
            logger.error(f"OpenAI API error in realtime mode: {str(e)}")
            raise

    def handle_error_with_llm(self, error_info: Dict[Any, Any], prev_response_id: str = None) -> tuple[bool, Optional[str]]:
        try:
            # 先提取错误关键信息
            messages = [
                {"role": "system", "content": "提取错误信息中的关键内容，用简洁的语言描述"},
                {"role": "user", "content": error_info['error']}
            ]
            
            completion = self.client.chat.completions.create(
                model=self.config.gptmodel_pre,
                messages=messages,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.7
            )
            
            extracted_error = completion.choices[0].message.content
            
            # 生成修复方案
            response = self.client.responses.create(
                model=self.config.gptmodel,
                input=extracted_error,
                instructions=self.prompt_manager._generate_error_prompt(error_info, self.context),
                previous_response_id=prev_response_id,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.7
            )
            
            response_text = response.output[0].content[0].text
            success = self._execute_error_fix(response_text, self.ui_manager, error_info)
            return success, response.id
        except Exception as e:
            logger.error(f"Error handling failed: {str(e)}")
            return False, None

class AIAssistantFactory:
    @staticmethod
    def create_assistant(config, ui_manager: Optional[UIManager] = None) -> BaseAIAssistant:
        gpt_model_name = config.starter.gptmodel if hasattr(config.starter, 'gptmodel') else "gpt-4o"
        openai_response_mode = config.starter.openai_response_mode if hasattr(config.starter, 'openai_response_mode') else False
        openai_realtime_mode = config.starter.openai_realtime_mode if hasattr(config.starter, 'openai_realtime_mode') else False
        
        if "deepseek" in gpt_model_name:
            return DeepseekAIAssistant(ui_manager=ui_manager)
        elif openai_response_mode:
            return OpenAIResponseAIAssistant(ui_manager=ui_manager)
        elif openai_realtime_mode:
            return OpenAIRealtimeAIAssistant(ui_manager=ui_manager)
        else:
            return OpenAIAssistant(ui_manager=ui_manager)