{% if not minimal %}
## 本回合任务：

{{ objective }}

{% if beat_action_brief %}
【本 Beat 核心行动目标】：{{ beat_action_brief }}
{% endif %}

{% if success_condition %}
成功条件：{{ success_condition }}
{% endif %}

{% if scene.location or scene.time_of_day or scene.weather or scene.mood %}
## 场景：
{% if scene.location %}- 当前地点：{{ scene.location }}{% endif %}
{% if scene.time_of_day %}- 时间：{{ scene.time_of_day }}{% endif %}
{% if scene.weather %}- 天气：{{ scene.weather }}{% endif %}
{% if scene.mood %}- 氛围：{{ scene.mood }}{% endif %}
{% endif %}

## 公开消息：

{{ history_summary }}
{% else %}
{# minimal mode: free chat, no task/scene/history injected #}
{% endif %}
