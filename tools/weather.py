"""Weather lookup using WeatherAPI."""

import requests
from config import Config


def get_weather(ciudad: str = None, dia: str = "hoy") -> str:
    if not Config.WEATHER_API_KEY:
        return "error: WEATHER_API_KEY not set in .env"

    ciudad = ciudad or Config.CITY

    # Request 2 days when asking for tomorrow, otherwise 1
    days_to_request = 2 if dia.lower() == "mañana" else 1

    url = (
        f"http://api.weatherapi.com/v1/forecast.json"
        f"?key={Config.WEATHER_API_KEY}"
        f"&q={ciudad}&days={days_to_request}&lang=es&aqi=no&alerts=no"
    )

    try:
        response = requests.get(url, timeout=5)

        if response.status_code != 200:
            return f"error: could not get the weather for {ciudad}"

        data = response.json()

        # index 0 = today, 1 = tomorrow
        index = 1 if dia.lower() == "mañana" else 0
        day_data = data["forecast"]["forecastday"][index]
        date      = day_data.get("date", "desconocida")
        temp_max  = day_data["day"].get("maxtemp_c", "N/A")
        temp_min  = day_data["day"].get("mintemp_c", "N/A")
        rain      = day_data["day"].get("daily_chance_of_rain", "N/A")
        condition = day_data["day"]["condition"]["text"].lower()

        # key=value format parsed by the Router
        if dia.lower() == "hoy":
            current_temp = data["current"].get("temp_c", "N/A")
            feels_like   = data["current"].get("feelslike_c", "N/A")
            return (
                f"ciudad={ciudad}, fecha={date}, dia=hoy, "
                f"temp_actual={current_temp}°C, sensacion={feels_like}°C, "
                f"condicion={condition}, maxima={temp_max}°C, "
                f"minima={temp_min}°C, prob_lluvia={rain}%"
            )
        else:
            return (
                f"ciudad={ciudad}, fecha={date}, dia=mañana, "
                f"condicion={condition}, maxima={temp_max}°C, "
                f"minima={temp_min}°C, prob_lluvia={rain}%"
            )

    except Exception as e:
        return f"error: could not connect to the weather API ({e})"
