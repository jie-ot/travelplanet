# {{ destination }}行程规划
{% if date_label %}
**日期：** {{ date_label }}
{% endif %}
{% if preparations %}

### 🎒 物资准备
{% for prep in preparations %}
- **{{ prep['category'] }}**: {{ prep['items'] }}
{% endfor %}
{% endif %}
{% if bookings %}

### 🎫 预订指南
{% for booking in bookings %}
- **{{ booking['type'] }}**: {{ booking['details'] }}
{% endfor %}
{% endif %}
{% if food_recommendations %}

### 🍜 美食推荐
{% for food in food_recommendations %}
- {{ food }}
{% endfor %}
{% endif %}
{% if days %}

### 🗓️ 行程安排
{% for day in days %}

#### 📍 {{ day['label'] }}
{% for sch in day['schedules'] %}
- **{{ sch['time_period'] }}**{% if sch['time_range'] %}（{{ sch['time_range'] }}）{% endif %}: {{ sch['activity'] }}
{% endfor %}
{% endfor %}
{% endif %}
