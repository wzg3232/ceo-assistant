#!/usr/bin/env python3
"""
PDF多发票识别器 - 支持单个PDF文件中包含多张发票（多页）
"""
import fitz
import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Optional


class PDFInvoiceExtractor:
    """从PDF提取发票信息，支持多页PDF每页一张发票"""
    
    def extract_all_invoices_by_file(self, pdf_dir: str) -> Dict[str, List[Dict]]:
        """提取目录中所有PDF文件的发票信息，返回文件名到发票列表的映射"""
        results = {}
        # 支持.pdf文件和无扩展名的code=文件（飞书下载附件）
        for pdf_file in Path(pdf_dir).glob('*.pdf'):
            invoices = self.extract_invoices(str(pdf_file))
            if invoices:
                results[pdf_file.name] = invoices
        
        # 处理code=开头的飞书下载附件
        for pdf_file in Path(pdf_dir).glob('code=*'):
            if pdf_file.suffix == '' or '.pdf' in pdf_file.name:
                invoices = self.extract_invoices(str(pdf_file))
                if invoices:
                    results[pdf_file.name] = invoices
        
        return results
    
    def extract_all_trip_summaries_by_file(self, pdf_dir: str) -> Dict[str, List[Dict]]:
        """提取目录中所有PDF文件的行程汇总信息（支持多种交通方式）"""
        results = {}
        for pdf_file in Path(pdf_dir).glob('*.pdf'):
            summaries = self.extract_trip_summaries(str(pdf_file))
            if summaries:
                results[pdf_file.name] = summaries
        return results
    
    def extract_invoices(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        提取PDF中所有发票（支持多页，每页可能是一张发票）
        """
        invoices = []
        
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            
            print(f"  处理 {Path(pdf_path).name}: {total_pages} 页")
            
            # 逐页提取
            for page_num in range(total_pages):
                text = doc[page_num].get_text()
                
                # 跳过纯行程单（滴滴、曹操等网约车行程单，非正式发票）
                # 判断标准：包含行程单关键字，但不包含发票关键字
                is_trip_summary = ('滴滴出行' in text or 'DIDI TRAVEL' in text or 
                                   '曹操出行' in text)
                has_invoice_kw = '发票号码' in text or '价税合计' in text
                
                if is_trip_summary and not has_invoice_kw:
                    continue  # 纯行程单，跳过
                
                # 检查这一页是否包含发票信息（扩大匹配范围）
                if any(kw in text for kw in ['发票号码', '价税合计', '通行费', '票价', '金额']):
                    invoice = self._parse_single_invoice(text, pdf_path, page_num + 1)
                    if invoice:
                        invoices.append(invoice)
            
            if not invoices:
                # 如果逐页没提取到，尝试全文提取
                full_text = ''.join(doc[page].get_text() for page in range(len(doc)))
                invoice = self._parse_single_invoice(full_text, pdf_path)
                if invoice:
                    invoices.append(invoice)
            
            doc.close()
            
            print(f"    提取到 {len(invoices)} 张发票")
            
        except Exception as e:
            print(f"    错误: {e}")
            import traceback
            traceback.print_exc()
        
        return invoices
    
    def extract_trip_summaries(self, pdf_path: str) -> List[Dict[str, Any]]:
        """从PDF中提取行程汇总信息（仅打车行程）"""
        summaries = []

        try:
            doc = fitz.open(pdf_path)

            for page_num in range(len(doc)):
                text = doc[page_num].get_text()

                # 仅提取打车行程汇总（滴滴、曹操等）
                if '笔行程' in text or '行程单' in text:
                    summary = self._parse_taxi_summary(text, pdf_path, page_num + 1)
                    if summary:
                        summaries.append(summary)

            doc.close()
        except Exception as e:
            print(f"  提取行程汇总错误: {e}")

        return summaries

    def _parse_taxi_summary(self, text: str, pdf_path: str, page_num: int) -> Dict[str, Any]:
        """解析打车行程汇总（滴滴、曹操等）"""
        summary = {
            'file_name': Path(pdf_path).name,
            'page': page_num,
            'trips': 0,
            'total_amount': 0,
            'start_date': '',
            'end_date': '',
            'phone': '',
            'trip_type': '其他',
        }

        # 判断行程类型
        if '滴滴出行' in text or 'DIDI TRAVEL' in text:
            summary['trip_type'] = '滴滴出行'
        elif '曹操出行' in text:
            summary['trip_type'] = '曹操出行'
        elif 'T3出行' in text:
            summary['trip_type'] = 'T3出行'

        # 提取行程数和金额
        match = re.search(r'共(\d+)笔行程[，,]\s*合计([¥￥]?\d+\.\d{2})元', text)
        if match:
            summary['trips'] = int(match.group(1))
            amount_str = match.group(2).replace('¥', '').replace('￥', '')
            try:
                summary['total_amount'] = float(amount_str)
            except:
                pass

        # 提取日期范围
        date_match = re.search(r'行程起止日期[：:]\s*(\d{4}-\d{2}-\d{2})\s+至\s+(\d{4}-\d{2}-\d{2})', text)
        if date_match:
            summary['start_date'] = date_match.group(1)
            summary['end_date'] = date_match.group(2)

        # 提取手机号
        phone_match = re.search(r'行程人手机号[：:]\s*(\d{11})', text)
        if phone_match:
            summary['phone'] = phone_match.group(1)

        # 提取每笔行程明细
        details = self._parse_trip_details(text)
        if details:
            summary['details'] = details

        return summary

    def _parse_trip_details(self, text: str) -> List[Dict[str, str]]:
        """提取每笔行程明细（处理换行分隔的格式）"""
        details = []

        # 找到所有行程段落（以序号开头，以里程和金额结尾）
        # 使用更简单的策略：按连续数字分割
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        # 找到表头行（包含"里程"的行）
        header_idx = -1
        for i, line in enumerate(lines):
            if '里程' in line or '公里' in line:
                header_idx = i
                break

        if header_idx == -1:
            return details

        # 表头后的内容
        data_lines = lines[header_idx + 1:]

        # 按序号分组（每组以数字开头）
        current_group = []
        for line in data_lines:
            if re.match(r'^\d+$', line) and current_group:
                # 处理上一组
                detail = self._parse_trip_group(current_group)
                if detail:
                    details.append(detail)
                current_group = [line]
            else:
                current_group.append(line)

        # 处理最后一组
        if current_group:
            detail = self._parse_trip_group(current_group)
            if detail:
                details.append(detail)

        return details

    def _parse_trip_group(self, lines: List[str]) -> Optional[Dict[str, str]]:
        """解析单个行程组"""
        if not lines or not re.match(r'^\d+$', lines[0]):
            return None

        seq = lines[0]

        # 找时间（格式：07-21 12:35 或 07-21 12:35 周二）
        time_idx = -1
        for i, line in enumerate(lines):
            # 匹配时间格式（可选带星期几）
            if re.match(r'^\d{2}-\d{2}\s+\d{2}:\d{2}(?:\s+(?:周一|周二|周三|周四|周五|周六|周日))?$', line):
                time_idx = i
                break

        if time_idx == -1:
            return None

        # 收集所有数字（应该是里程和金额）
        numbers = [(i, l) for i, l in enumerate(lines) if re.match(r'^[\d.]+$', l)]
        if len(numbers) < 2:
            return None

        # 最后两个数字是里程和金额
        distance = numbers[-2][1]
        amount = numbers[-1][1]
        amount_idx = numbers[-1][0]

        # 车型是时间前的非时间文本
        type_parts = []
        for line in lines[1:time_idx]:
            if not re.match(r'^\d{2}-\d{2}\s+\d{2}:\d{2}$', line):
                type_parts.append(line)
        trip_type = ' '.join(type_parts).strip()

        # 城市是时间后、地点前的文本（跳过星期几）
        city_parts = []
        start_idx = time_idx + 1
        for line in lines[start_idx:amount_idx]:
            if line in ['周一', '周二', '周三', '周四', '周五', '周六', '周日']:
                continue
            if re.match(r'^[\d.]+$', line):
                break
            # 检查是否是城市关键词（单字或简短中文）
            if re.match(r'^[\u4e00-\u9fff]{1,4}$', line):
                city_parts.append(line)
            else:
                break
        city = ' '.join(city_parts).strip()

        # 起点和终点是城市和数字之间的文本
        location_parts = []
        in_location = False
        for line in lines[start_idx:amount_idx]:
            if line in ['周一', '周二', '周三', '周四', '周五', '周六', '周日']:
                continue
            if re.match(r'^[\d.]+$', line):
                break
            if not in_location:
                # 跳过城市部分（简短中文）
                if re.match(r'^[\u4e00-\u9fff]{1,3}$', line):
                    continue
                else:
                    in_location = True
            if in_location:
                location_parts.append(line)

        # 合并地点文本并按竖线分隔
        # 注意：location_parts包含被换行分割的片段
        # 格式是: "起点区域|起点地点" 和 "终点区域|终点地点" 在不同行
        # 需要正确分组：第一个分组是起点，第二个分组是终点

        # 预处理：合并被换行分割的片段
        # 如果下一行以特殊符号开头，说明是上一行的续接
        # 但如果当前行包含竖线，说明是新地址段的开始，不合并
        merged_parts = []
        for part in location_parts:
            if merged_parts:
                last = merged_parts[-1]
                # 如果当前行包含竖线，说明是新地址段的开始
                if '|' in part:
                    should_merge = False
                else:
                    # 检查是否需要合并（续接）
                    should_merge = (
                        part.startswith('(') or part.startswith('（') or
                        part.startswith('-') or part.startswith('）') or
                        part.startswith(')') or part.startswith('·') or
                        part.startswith(',') or part.startswith(',') or
                        part.startswith('.') or part.startswith('。') or
                        # 如果上一行以未闭合括号结尾
                        last.endswith('(') or last.endswith('（') or last.endswith('-') or
                        # 如果当前行很短且上一行以字母/数字结尾（中文分行）
                        (len(part) < 15 and last and last[-1].isalnum())
                    )
                if should_merge:
                    merged_parts[-1] = merged_parts[-1] + part
                else:
                    merged_parts.append(part)
            else:
                merged_parts.append(part)

        # 现在merged_parts包含合并后的地址片段
        # 格式：["起点完整地址", "终点完整地址"] 或 ["起点完整地址"]
        # 每个片段可能包含一个或多个"区域|地点"对
        all_locations = []
        for part in merged_parts:
            # 按竖线分割，每个"区域|地点"组成一个完整地址
            parts = part.split('|')
            for i, p in enumerate(parts):
                if p.strip():
                    # 如果是第一个部分，直接使用
                    # 如果是后续部分，与上一个部分合并
                    if i == 0:
                        all_locations.append(p.strip())
                    else:
                        # 与前一个地址合并
                        if all_locations:
                            all_locations[-1] = f"{all_locations[-1]} {p.strip()}".strip()

        # 过滤空字符串
        all_locations = [loc for loc in all_locations if loc]

        # 配对：第一个是起点，第二个是终点
        if len(all_locations) >= 2:
            from_loc = all_locations[0]
            to_loc = all_locations[1]
        elif len(all_locations) == 1:
            from_loc = all_locations[0]
            to_loc = ''
        else:
            from_loc = ' '.join(merged_parts)
            to_loc = ''

        return {
            'seq': seq,
            'type': trip_type,
            'time': lines[time_idx],
            'city': city,
            'from': from_loc,
            'to': to_loc,
            'distance': distance,
            'amount': amount,
        }

    def _detect_invoice_type(self, text: str) -> str:
        """
        识别发票类型，用于后续金额提取策略
        返回: 'standard' | 'train' | 'toll_special' | 'flight_itinerary'
        """
        # 1. 铁路电子客票
        if '电子发票（铁路电子客票）' in text or '票价:￥' in text or '票价:¥' in text:
            return 'train'
        
        # 2. 航空运输电子客票行程单（非发票）
        if '航空运输电子客票行程单' in text:
            return 'flight_itinerary'
        
        # 3. 通行费发票（特殊格式，无"价税合计"字段）
        if '通行费' in text and '价税合计' not in text:
            return 'toll_special'
        
        # 4. 通用电子发票（有"价税合计"字段）
        if '价税合计' in text or '电子发票' in text:
            return 'standard'
        
        # 默认
        return 'standard'
    
    def _extract_amount_by_type(self, text: str, merged_text: str, invoice_type: str) -> float:
        """根据发票类型采用不同的金额提取策略"""
        
        if invoice_type == 'train':
            return self._extract_train_amount(text, merged_text)
        elif invoice_type == 'toll_special':
            return self._extract_toll_special_amount(text, merged_text)
        elif invoice_type == 'flight_itinerary':
            return self._extract_flight_itinerary_amount(text, merged_text)
        else:
            return self._extract_standard_amount(text, merged_text)
    
    def _extract_train_amount(self, text: str, merged_text: str) -> float:
        """铁路电子客票：提取'票价:￥xxx'或'票价:¥xxx'"""
        # 格式: 票价:￥367.00 或 票价:¥367.00
        match = re.search(r'票价[：:]\s*[¥￥]\s*(\d+\.?\d*)', merged_text)
        if match:
            return float(match.group(1))
        # 兜底：匹配所有金额取最大
        return self._extract_standard_amount(text, merged_text)
    
    def _extract_toll_special_amount(self, text: str, merged_text: str) -> float:
        """
        特殊通行费发票（非通用格式）：
        没有"价税合计"字段，金额字段是"金额合计（小写）"或直接是金额
        格式示例: 肆拾壹元整 41.00
        """
        # 优先匹配"金额合计（小写）"后的金额
        match = re.search(r'金额合计[（(]小写[）)]\s*[：:]?\s*[¥￥]?\s*(\d+\.?\d*)', merged_text)
        if match:
            return float(match.group(1))
        
        # 匹配大写金额后的阿拉伯数字（如"肆拾壹元整"后面的"41.00"）
        # 找大写金额（X元整/X元X角X分）
        upper_amount = re.search(r'[零壹贰叁肆伍陆柒捌玖拾佰仟]+元[零壹贰叁肆伍陆柒捌玖拾佰仟角分]*整?', merged_text)
        if upper_amount:
            # 在大写金额后找阿拉伯数字
            after_upper = merged_text[upper_amount.end():]
            match = re.search(r'(\d+\.\d{2})', after_upper)
            if match:
                return float(match.group(1))
        
        # 兜底：取所有金额中的最大值
        return self._extract_standard_amount(text, merged_text)
    
    def _extract_flight_itinerary_amount(self, text: str, merged_text: str) -> float:
        """航空运输电子客票行程单：提取'合计'金额"""
        # 格式: 合计 ¥1440.00 或 合计 1440.00
        match = re.search(r'合计\s*[¥￥]?\s*(\d+\.?\d*)', merged_text)
        if match:
            return float(match.group(1))
        return self._extract_standard_amount(text, merged_text)
    
    def _extract_standard_amount(self, text: str, merged_text: str) -> float:
        """
        通用电子发票：提取"价税合计（小写）"后的金额
        支持格式:
        1. 标准格式: 价税合计（小写）¥59.00
        2. 换行格式: 价税合计（大写）\n（小写）\n106.80\n¥
        3. 复杂格式: 包含优惠、小计等多行金额
        """
        # 1. 优先匹配"价税合计（小写）"后的金额（标准格式）
        small_write_match = re.search(r'(?:价税合计|金额合计)[（(]小写[）)]\s*[：:]?\s*[¥￥]?\s*(\d+\.\d{2})', merged_text)
        if small_write_match:
            return float(small_write_match.group(1))
        
        # 2. 处理文本被换行分割的情况（如：价税合计（大写）\n（小写）\n106.80）
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if '价税合计' in line or '金额合计' in line:
                # 检查后面几行内是否有金额
                for j in range(i+1, min(i+8, len(lines))):
                    next_line = lines[j].strip()
                    # 优先匹配小写标签后的金额
                    if '（小写）' in lines[j-1] or '小写' in lines[j-1]:
                        amount_match = re.match(r'^([¥￥]?\s*\d+\.\d{2})$', next_line)
                        if amount_match:
                            amount_str = amount_match.group(1).replace('¥', '').replace('￥', '').strip()
                            try:
                                return float(amount_str)
                            except:
                                pass
                        break
                    # 匹配独立的金额数字
                    amount_match = re.match(r'^([¥￥]?\s*\d+\.\d{2})$', next_line)
                    if amount_match and '（小写）' in str(lines[i:i+3]):
                        amount_str = amount_match.group(1).replace('¥', '').replace('￥', '').strip()
                        try:
                            return float(amount_str)
                        except:
                            pass
                        break
        
        # 3. 兜底：匹配 ¥ 或 ￥ 后跟随的金额（最可靠的方式）
        yuan_matches = re.findall(r'[¥￥]\s*(\d+\.\d{2})', text)
        if yuan_matches:
            return float(yuan_matches[-1])  # 取最后一个（通常是合计）
        
        # 4. 其他兜底匹配
        all_amounts = []
        all_amounts.extend(re.findall(r'[¥￥]\s*(\d+\.\d{2})', merged_text))
        all_amounts.extend(re.findall(r'(?:发票总金额|运单金额|总金额)\s*[（(][^）)]*?[）)]\s*[:：]?\s*(\d+\.\d{2})', merged_text))
        
        # 过滤并取最大值
        amounts = []
        for a in all_amounts:
            try:
                val = float(a)
                if val >= 1 and val < 10000:
                    amounts.append(val)
            except:
                pass
        return max(amounts) if amounts else 0
    
    def _parse_single_invoice(self, text: str, pdf_path: str, page_num: int = 1) -> Dict[str, Any]:
        """解析单个发票文本 - 根据发票类型采用不同的提取策略"""
        invoice = {
            'file_name': Path(pdf_path).name,
            'page': page_num,
            'invoice_number': '',
            'invoice_date': '',
            'total_amount': 0,
            'buyer_name': '',
            'seller_name': '',
            'item_name': '',
        }
        
        # 识别发票类型
        invoice_type = self._detect_invoice_type(text)
        invoice['invoice_type'] = invoice_type
        
        # 预处理：合并连续的行，去除多余空白
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        merged_text = ' '.join(lines)
        
        # 根据发票类型采用不同的解析策略
        if invoice_type == 'train':
            self._parse_train_invoice(text, merged_text, invoice)
        elif invoice_type == 'toll_special':
            self._parse_toll_special_invoice(text, merged_text, invoice)
        elif invoice_type == 'flight_itinerary':
            self._parse_flight_itinerary(text, merged_text, invoice)
        else:
            self._parse_standard_invoice(text, merged_text, invoice)
        
        # 保存OCR原文（前500字符用于展示）
        invoice['ocr_text'] = text[:500] if len(text) > 500 else text
        
        # 如果找到了关键信息，认为是有效发票
        if invoice['total_amount'] > 0 or invoice['invoice_number']:
            return invoice
        
        return None
    
    def _parse_standard_invoice(self, text: str, merged_text: str, invoice: Dict):
        """解析通用电子发票"""
        # 1. 发票号码
        match = re.search(r'发票号码[：:]\s*(\d{15,20})', merged_text)
        if match:
            invoice['invoice_number'] = match.group(1)
        else:
            # 兜底：匹配20位数字
            match = re.search(r'(\d{20})', merged_text)
            if match and match.group(1).startswith('2'):
                invoice['invoice_number'] = match.group(1)
        
        # 2. 开票日期 - 多种格式
        # 格式1: 开票日期：2026年07月28日
        match = re.search(r'开票日期[：:]\s*(\d{4}年\d{1,2}月\d{1,2}日)', merged_text)
        if match:
            invoice['invoice_date'] = match.group(1)
        else:
            # 格式2: 直接出现日期（如"2026年07月28日"）
            match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', merged_text)
            if match:
                invoice['invoice_date'] = match.group(1)
        
        # 3. 购买方名称和销售方名称
        # 策略：找到所有包含"有限公司"或"中心"、"指挥部"等的文本段
        company_matches = re.findall(r'(?:名称[：:])?\s*([^\s]+?(?:有限公司|中心|指挥部|事务所|服务站))', merged_text)
        if len(company_matches) >= 2:
            # 清理前缀
            buyer = company_matches[0].strip()
            seller = company_matches[1].strip()
            # 移除可能的"名称："前缀
            buyer = re.sub(r'^名称[：:]\s*', '', buyer)
            seller = re.sub(r'^名称[：:]\s*', '', seller)
            invoice['buyer_name'] = buyer
            invoice['seller_name'] = seller
        elif len(company_matches) == 1:
            buyer = company_matches[0].strip()
            buyer = re.sub(r'^名称[：:]\s*', '', buyer)
            invoice['buyer_name'] = buyer
        
        # 4. 统一社会信用代码
        # 购买方税号通常在"购买方信息"区域
        buyer_code_match = re.search(r'购买方信息[^销]*?统一社会信用代码[：:]\s*([A-Z0-9]{15,20})', merged_text, re.DOTALL)
        if buyer_code_match:
            invoice['buyer_credit_code'] = buyer_code_match.group(1)
        
        seller_code_match = re.search(r'销售方信息[^购]*?统一社会信用代码[：:]\s*([A-Z0-9]{15,20})', merged_text, re.DOTALL)
        if seller_code_match:
            invoice['seller_credit_code'] = seller_code_match.group(1)
        
        # 兜底：提取所有18位代码（排除发票号码）
        if not invoice.get('buyer_credit_code'):
            all_codes = re.findall(r'([A-Z0-9]{18})', merged_text)
            codes = [c for c in all_codes if not (c.isdigit() and c.startswith('2'))]
            if codes:
                invoice['buyer_credit_code'] = codes[0]
                if len(codes) > 1:
                    invoice['seller_credit_code'] = codes[1]
        
        # 5. 金额
        invoice['total_amount'] = self._extract_standard_amount(text, merged_text)
        
        # 6. 购买项目 - 匹配"*商品名*规格"格式，但要限制范围
        match = re.search(r'\*([^*\n]{1,30})\*([^*\n]{1,50})', merged_text)
        if match:
            # 清理项目名，去除多余的空格和数字
            item_name = f"*{match.group(1).strip()}*{match.group(2).strip()}"
            # 如果项目名包含太多数字或特殊字符，截取前面部分
            if len(item_name) > 60:
                item_name = item_name[:60] + "..."
            invoice['item_name'] = item_name
    
    def _parse_train_invoice(self, text: str, merged_text: str, invoice: Dict):
        """解析铁路电子客票"""
        # 1. 发票号码（格式：发票号码:26119110010006896352）
        match = re.search(r'发票号码[：:]\s*(\d{20})', merged_text)
        if match:
            invoice['invoice_number'] = match.group(1)
        
        # 2. 开票日期
        match = re.search(r'开票日期[：:]\s*(\d{4}年\d{1,2}月\d{1,2}日)', merged_text)
        if match:
            invoice['invoice_date'] = match.group(1)
        
        # 3. 购买方名称（格式：购买方名称:北京紫冬花能源科技有限公司）
        # 铁路发票文本是竖排的，合并后需要精确匹配
        match = re.search(r'购买方名称[：:]\s*(\S+?有限公司)', merged_text)
        if match:
            invoice['buyer_name'] = match.group(1).strip()
        else:
            # 兜底：直接查找"北京紫冬花"或类似的公司名
            match = re.search(r'((?:北京|上海|广州|深圳)\S{2,20}有限公司)', merged_text)
            if match:
                invoice['buyer_name'] = match.group(1).strip()
        
        # 4. 购买方税号（格式：统一社会信用代码:91110119MABX263Y15）
        match = re.search(r'统一社会信用代码[：:]\s*([A-Z0-9]{18})', merged_text)
        if match:
            invoice['buyer_credit_code'] = match.group(1)
        
        # 5. 销售方（铁路发票特殊，销售方是铁路局）
        invoice['seller_name'] = '中国铁路'
        
        # 6. 金额（格式：票价:￥367.00）
        invoice['total_amount'] = self._extract_train_amount(text, merged_text)
        
        # 7. 购买项目（铁路票通常是"客运服务"）
        invoice['item_name'] = '*交通运输服务*铁路客运'
    
    def _parse_toll_special_invoice(self, text: str, merged_text: str, invoice: Dict):
        """解析特殊通行费发票（非通用格式）"""
        # 这种发票没有标准的发票号码、日期、购买方/销售方信息
        # 需要从文本中推断
        
        # 1. 发票号码（特殊通行费通常没有，留空）
        invoice['invoice_number'] = ''
        
        # 2. 开票日期（从文本中提取日期）
        match = re.search(r'(\d{4}-\d{2}-\d{2})', merged_text)
        if match:
            date_str = match.group(1).replace('-', '年') + '日'
            date_str = date_str.replace('年', '年').replace('日', '月').replace('日', '日')
            # 转换格式：2026-07-28 -> 2026年07月28日
            parts = match.group(1).split('-')
            invoice['invoice_date'] = f"{parts[0]}年{parts[1]}月{parts[2]}日"
        
        # 3. 购买方名称（从上下文推断，通常是公司名）
        # 特殊通行费发票通常没有明确的购买方标签，需要从文件名或上下文推断
        # 这里暂时留空，后续可以从审批单信息中补充
        invoice['buyer_name'] = ''
        
        # 4. 销售方（从"入口站"推断）
        match = re.search(r'入口站[：:]\s*([^\n]+)', merged_text)
        if match:
            invoice['seller_name'] = match.group(1).strip()
        
        # 5. 金额
        invoice['total_amount'] = self._extract_toll_special_amount(text, merged_text)
        
        # 6. 购买项目
        invoice['item_name'] = '*生产生活服务*高速公路通行费'
    
    def _parse_flight_itinerary(self, text: str, merged_text: str, invoice: Dict):
        """解析航空运输电子客票行程单"""
        # 1. 发票号码（行程单通常没有发票号码）
        invoice['invoice_number'] = ''
        
        # 2. 开票日期
        match = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', merged_text)
        if match:
            invoice['invoice_date'] = match.group(1)
        
        # 3. 购买方名称
        match = re.search(r'([^\n]+?有限公司)', merged_text)
        if match:
            invoice['buyer_name'] = match.group(1).strip()
        
        # 4. 销售方（航空公司）
        if '南方航空' in merged_text:
            invoice['seller_name'] = '中国南方航空股份有限公司'
        elif '东方航空' in merged_text:
            invoice['seller_name'] = '中国东方航空股份有限公司'
        elif '国际航空' in merged_text or '国航' in merged_text:
            invoice['seller_name'] = '中国国际航空股份有限公司'
        
        # 5. 金额
        invoice['total_amount'] = self._extract_flight_itinerary_amount(text, merged_text)
        
        # 6. 购买项目
        invoice['item_name'] = '*交通运输服务*航空客运'


def main():
    """测试多页PDF解析"""
    pdf_dir = Path('D:/program/feishu-approval-ai/data/temp')
    extractor = PDFInvoiceExtractor()
    
    print("=" * 50)
    print("PDF多发票提取测试")
    print("=" * 50)
    
    # 测试关键PDF文件
    test_files = ['餐费.pdf', '高铁.pdf', '住宿.pdf', '其他车票.pdf']
    
    for filename in test_files:
        pdf_path = pdf_dir / filename
        if pdf_path.exists():
            invoices = extractor.extract_invoices(str(pdf_path))
            print(f"\n{filename}: {len(invoices)} 张发票")
            for inv in invoices:
                print(f"  - 发票号: {inv.get('invoice_number', 'N/A')}, 金额: ¥{inv.get('total_amount', 0):.2f}, 页码: {inv.get('page')}")
        else:
            print(f"\n{filename}: 文件不存在")
    
    # 统计所有PDF
    print("\n" + "=" * 50)
    print("所有PDF文件统计:")
    
    total_invoices = 0
    for pdf_file in sorted(pdf_dir.glob('*.pdf')):
        invoices = extractor.extract_invoices(str(pdf_file))
        if invoices:
            total_invoices += len(invoices)
            print(f"  {pdf_file.name}: {len(invoices)} 张")
    
    print(f"\n总计: {total_invoices} 张发票 (来自 {len(list(pdf_dir.glob('*.pdf')))} 个PDF)")


if __name__ == '__main__':
    main()
