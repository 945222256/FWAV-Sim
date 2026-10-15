# -*- coding: utf-8 -*-
"""api_lab.py — 参数实验室页面只读接口（I-130 阶段一，云端侧）。

数据纪律：页面显示的每一个数值实时读自 params 镜像表（本地登记库
经代理上行同步的权威内容），本模块不存任何参数值。载荷构建逻辑在
lab_payload.py（与本地看板服务共用同一份，部署时随本文件一起上传）。
设计依据：需求定稿 D-047、设计方案四稿、参数分级架构 D-045、
I-127 接口契约。仅使用 Python 标准库。
"""
import json

import auth
import db
import repo
import router

import lab_payload
import api_param_semantics  # I-232 metadata-only writes share this deployed entry point.


@router.route("GET", "/api/lab/params")
def lab_params(ctx):
    """参数实验室阶段一陈列载荷：一切数值实时读自镜像表。"""
    auth.caller(ctx)
    params = repo.all_params()
    archive = [json.loads(r["payload"]) for r in
               db.query("SELECT payload FROM params_archive ORDER BY seq")]
    return {"ok": True, "result": lab_payload.build_payload(params, archive)}
