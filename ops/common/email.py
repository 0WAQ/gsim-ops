import subprocess
from typing import Any
from pathlib import Path
from datetime import datetime

'''
class Email:
    def __init__(self):
        self.config = None


    def get_author_info(self, author_key):
        """获取作者信息"""
        author_data = self.config.authors.get(author_key, {})
        return {
            'key': author_key,
            'name': author_data.get('name', author_key),
            'email': author_data.get('email')
        }
    
    def group_by_author(self, results: dict[Path, dict[str, Any]]):
        """按作者分组因子"""
        grouped = defaultdict(list)
        for factor_dir, result in results.items():
            result['factor_name'] = factor_dir
            result['author_key'] = self.user
            grouped[self.user].append(result)
        return grouped
    
    def get_checkpoint_status(self, factor_name, checkpoint_result):
        """获取断点检测状态"""
        # if not checkpoint_result or checkpoint_result.get('status') != 'DONE':
        #     return 'SKIP', '未检测'
        print(checkpoint_result)
        if factor_name in checkpoint_result.get('passed', []):
            return 'PASS', '✅ 通过'
        elif factor_name in checkpoint_result.get('failed', []):
            return 'FAIL', '❌ 失败 (MD5不一致)'
        elif factor_name in checkpoint_result.get('missing', []):
            return 'MISSING', '⚠️ 缺失文件'
        else:
            return 'SKIP', '未检测'
    
    def get_correlation_status(self, factor_name, correlation_results):
        """获取相关性检测状态"""
        if not correlation_results or factor_name not in correlation_results:
            return 'SKIP', '未检测', {}
        
        corr_result = correlation_results[factor_name]
        status = corr_result.get('status')
        
        if status == 'PASS':
            return 'LOW_CORR', '✅ 通过 (低相关性)', corr_result
        elif status == 'PASS_BEAT':
            return 'BEAT_HIGH_CORR', '✅ 打败高相关因子', corr_result
        elif status == 'FAIL':
            return 'FAIL', '❌ 未打败高相关因子', corr_result
        elif status == 'ERROR':
            return 'ERROR', f"⚠️ 错误: {corr_result.get('error', '未知')}", corr_result
        else:
            return 'SKIP', '未检测', {}
    
    def build_personal_content(self, author_name, factors, correlation_results=None, checkpoint_result=None):
        """构建个人邮件内容"""
        content = f"**{author_name}，您好！**\n\n"
        content += f"以下是您的因子完整检测结果 (合规性 → 相关性 → 断点)：\n\n"
        content += "---\n\n"
        
        # 按因子展示
        for i, f in enumerate(factors, 1):
            fname = f['factor_name']
            content += f"### {i}. {fname}\n\n"
            
            # 合规性检测
            content += "**📋 合规性检测**\n"
            if f['status'] == 'PASS':
                content += f"   • 状态：✅ 通过\n"
                content += f"   • 检测文件数：{f['total_checked']} 个\n"
                content += f"   • 平均多头持仓：{f.get('avg_long', 0):.2f}% (平均 {f.get('avg_long_count', 0):.0f} 只)\n"
                content += f"   • 平均空头持仓：{f.get('avg_short', 0):.2f}% (平均 {f.get('avg_short_count', 0):.0f} 只)\n"
            elif f['status'] == 'FAIL':
                content += f"   • 状态：❌ 失败\n"
                content += f"   • 失败日期：{f['date']}\n"
                content += f"   • 失败原因：{f['error']}\n"
                content += f"   • 平均多头持仓：{f.get('avg_long', 0):.2f}% (平均 {f.get('avg_long_count', 0):.0f} 只)\n"
                content += f"   • 平均空头持仓：{f.get('avg_short', 0):.2f}% (平均 {f.get('avg_short_count', 0):.0f} 只)\n"
            else:
                content += f"   • 状态：⚠️ {f['status']}\n"
                content += f"   • 原因：{f.get('reason', '未知')}\n"
            
            # 相关性检测
            if self.correlation_enable:
                corr_status, corr_msg, corr_data = self.get_correlation_status(fname, correlation_results)
                content += f"\n**🔗 相关性检测**\n"
                content += f"   • 状态：{corr_msg}\n"
                
                if corr_data:
                    m = corr_data.get('target_metrics', {})
                    mc = corr_data.get('max_corr', 0)
                    mcf = corr_data.get('max_corr_factor', 'N/A')
                    hc = corr_data.get('high_corr_count', 0)
                    
                    content += f"   • 因子指标：ret={m.get('ret', 0):.1f}% shrp={m.get('shrp', 0):.2f} fit={m.get('fitness', 0):.2f}\n"
                    content += f"   • 最大相关性：{mc:.3f} (与 {mcf})\n"
                    
                    if hc > 0:
                        content += f"   • 高相关因子数：{hc} 个\n"
                    
                    if corr_status == 'FAIL':
                        unbeaten = corr_data.get('unbeaten_example')
                        if unbeaten:
                            uf_name, uf_corr, uf_m = unbeaten
                            content += f"   • 未打败示例：{uf_name} (corr={uf_corr:.3f})\n"
                            content += f"     - 对方指标：ret={uf_m['ret']:.1f}% shrp={uf_m['shrp']:.2f} fit={uf_m['fitness']:.2f}\n"
            
            # 断点检测
            if self.checkpoint_enable:
                ckpt_status, ckpt_msg = self.get_checkpoint_status(fname, checkpoint_result)
                content += f"\n**🔄 断点检测**\n"
                content += f"   • 状态：{ckpt_msg}\n"
            
            content += "\n---\n\n"
        
        # 总结
        comp_pass = sum(1 for f in factors if f['status'] == 'PASS')
        comp_fail = sum(1 for f in factors if f['status'] == 'FAIL')
        
        content += "**📊 总结**\n\n"
        content += f"• 合规性检测：✅ {comp_pass} 个通过，❌ {comp_fail} 个失败\n"
        
        if correlation_results:
            factor_names = [f['factor_name'] for f in factors]
            corr_low = sum(1 for fn in factor_names 
                          if correlation_results.get(fn, {}).get('status') == 'PASS')
            corr_beat = sum(1 for fn in factor_names 
                           if correlation_results.get(fn, {}).get('status') == 'PASS_BEAT')
            corr_fail = sum(1 for fn in factor_names 
                           if correlation_results.get(fn, {}).get('status') == 'FAIL')
            content += f"• 相关性检测：✅ {corr_low} 个低相关，🎯 {corr_beat} 个打败高相关，❌ {corr_fail} 个失败\n"
        
        if checkpoint_result and checkpoint_result.get('status') == 'DONE':
            factor_names = [f['factor_name'] for f in factors]
            ckpt_pass = sum(1 for fn in factor_names if fn in checkpoint_result.get('passed', []))
            ckpt_fail = sum(1 for fn in factor_names if fn in checkpoint_result.get('failed', []))
            content += f"• 断点检测：✅ {ckpt_pass} 个通过，❌ {ckpt_fail} 个失败\n"
        
        return content

    def build_summary_content(self, all_results, correlation_results=None, checkpoint_results=None):
        """构建汇总邮件内容"""
        content = f"**检测时间：** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        content += "---\n\n"
        
        # 总体统计
        comp_pass = sum(1 for r in all_results.values() if r['status'] == 'PASS')
        comp_fail = sum(1 for r in all_results.values() if r['status'] == 'FAIL')
        total = len(all_results)
        
        content += "**📊 总体统计**\n\n"
        content += f"• 检测因子总数：{total} 个\n"
        content += f"• 合规性检测：✅ {comp_pass} 个通过，❌ {comp_fail} 个失败\n"
        
        if correlation_results:
            corr_low = sum(1 for r in correlation_results.values() if r.get('status') == 'PASS')
            corr_beat = sum(1 for r in correlation_results.values() if r.get('status') == 'PASS_BEAT')
            corr_fail = sum(1 for r in correlation_results.values() if r.get('status') == 'FAIL')
            corr_error = sum(1 for r in correlation_results.values() if r.get('status') == 'ERROR')
            content += f"• 相关性检测：✅ {corr_low} 个低相关，🎯 {corr_beat} 个打败高相关，❌ {corr_fail} 个失败，⚠️ {corr_error} 个错误\n"
        
        if checkpoint_results and checkpoint_results.get('status') == 'DONE':
            ckpt_pass = len(checkpoint_results.get('passed', []))
            ckpt_fail = len(checkpoint_results.get('failed', []))
            ckpt_miss = len(checkpoint_results.get('missing', []))
            content += f"• 断点检测：✅ {ckpt_pass} 个通过，❌ {ckpt_fail} 个失败，⚠️ {ckpt_miss} 个缺失\n"
        
        content += "\n---\n\n"
        
        # 按因子详细展示
        content += "**📋 详细结果**\n\n"
        
        for i, (fname, r) in enumerate(sorted(all_results.items()), 1):
            author = self.get_author_info(self.user)['name']
            
            content += f"### {i}. {fname} (作者: {author})\n\n"
            
            # 合规性检测
            if r['status'] == 'PASS':
                content += f"   • 合规性：✅ 通过 ({r['total_checked']} 个文件)\n"
                content += f"      - 平均多头：{r.get('avg_long', 0):.2f}% ({r.get('avg_long_count', 0):.0f} 只)\n"
                content += f"      - 平均空头：{r.get('avg_short', 0):.2f}% ({r.get('avg_short_count', 0):.0f} 只)\n"
            elif r['status'] == 'FAIL':
                content += f"   • 合规性：❌ 失败\n"
                content += f"      - 失败日期：{r['date']}\n"
                content += f"      - 失败原因：{r['error']}\n"
                content += f"      - 平均多头：{r.get('avg_long', 0):.2f}% ({r.get('avg_long_count', 0):.0f} 只)\n"
                content += f"      - 平均空头：{r.get('avg_short', 0):.2f}% ({r.get('avg_short_count', 0):.0f} 只)\n"
            else:
                content += f"   • 合规性：⚠️ {r['status']} - {r.get('reason', '未知')}\n"
            
            # 相关性检测
            if self.correlation_enable:
                corr_status, corr_msg, corr_data = self.get_correlation_status(fname, correlation_results)
                content += f"   • 相关性检测：{corr_msg}\n"
                
                if corr_data and corr_data.get('status') not in ['ERROR', None]:
                    m = corr_data.get('target_metrics', {})
                    mc = corr_data.get('max_corr', 0)
                    hc = corr_data.get('high_corr_count', 0)
                    
                    content += f"      - 指标：ret={m.get('ret', 0):.1f}% shrp={m.get('shrp', 0):.2f} fit={m.get('fitness', 0):.2f}\n"
                    content += f"      - 最大相关：{mc:.3f} (高相关数：{hc})\n"
                    
                    if corr_status == 'FAIL':
                        unbeaten = corr_data.get('unbeaten_example')
                        if unbeaten:
                            uf_name, _, uf_m = unbeaten
                            content += f"      - 未打败：{uf_name} (fit={uf_m['fitness']:.2f})\n"
            
            # 断点检测
            if self.checkpoint_enable:
                ckpt_status, ckpt_msg = self.get_checkpoint_status(fname, checkpoint_results)
                content += f"   • 断点检测：{ckpt_msg}\n"
            
            content += "\n"
        
        return content

    def send_email(self, to_email, title, content):
        """发送邮件"""
        if self.config.dry_run:
            print(f"  🔍 [Dry-run] 邮件将发送到: {to_email}")
            print(f"  📧 标题: {title}")
            print(f"  📄 内容预览:\n{content[:300]}...\n")
            return True
        
        try:
            result = subprocess.run(
                [self.config.python_path, self.config.feishu_script, '--email', to_email,
                 '--card', '--title', title, '--content', content],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                print(f"  ✅ 邮件已发送到: {to_email}")
                return True
            else:
                print(f"  ❌ 邮件发送失败: {result.stderr}")
                return False
        except Exception as e:
            print(f"  ❌ 邮件发送异常: {e}")
            return False

    def send(self, compliance_results, correlation_results, checkpoint_results):
        # ========== 统一发送邮件 ==========
        print("=" * 80)
        print("📧 发送检测报告")
        print("=" * 80 + "\n")
        
        grouped = self.group_by_author(compliance_results)
        # 个人邮件
        if self.config.send_author_email:
            print("📨 发送个人邮件:\n")
            for author_key, factors in grouped.items():
                if not author_key:
                    continue
                author_info = self.get_author_info(author_key)
                if not author_info['email']:
                    print(f"  ⚠️ 跳过 {author_info['name']} (未找到邮箱)")
                    continue
                
                content = self.build_personal_content(
                    author_info['name'], 
                    factors, 
                    correlation_results,
                    checkpoint_results
                )
                title = f"您的因子完整检测报告"
                self.send_email(author_info['email'], title, content)
        
        # 汇总邮件
        if self.config.summary_emails:
            print("\n📨 发送汇总邮件:\n")
            content = self.build_summary_content(
                compliance_results, 
                correlation_results,
                checkpoint_results
            )
            title = "因子完整检测汇总报告"
            for email in self.config.summary_emails:
                self.send_email(email, title, content)
        
        print()
        print("=" * 80)
        print("✅ 检测完成")
        print("=" * 80)
'''