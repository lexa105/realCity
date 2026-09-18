import os
import json
from dotenv import load_dotenv
from typing import Optional, Literal
from pydantic import BaseModel, Field
from geopy.geocoders import Nominatim

from apify_client import ApifyClient

load_dotenv()


# Unified listing model for the Map.
class UnifiedListing(BaseModel):
    source: Literal["bezrealitky", "sreality", "bazos"]
    external_id: str
    url: str
    title: str
    listing_type: Literal["rent", "sale"]
    disposition: Optional[str]   # "1+kk", "2+1", etc.
    price_czk: int
    address: str
    locality: str
    image_url: Optional[str] = None


# APIfy integration
def fetching_apify(provider: str, limit: int = 10) -> list[dict]:
    client = ApifyClient(os.getenv("APIFY_API_TOKEN"))
    actors_dict = {
        "bezrealitky": "QsjkAHuaFwcSxukzl",
        "sreality": "JiE5t9RDdD5bf4ABM"
    }

    PROVIDERS = {
            "bezrealitky": {
                "actor_id": "QsjkAHuaFwcSxukzl"
            },
            "sreality": {
                "actor_id": "JiE5t9RDdD5bf4ABM"
            }
        }

    # Prepare the Actor input
    bezrealitky_input = {   
        "includeDetails": True,
        "landType": "building",
        "language": "cs",
        "location": "Praha",
        "maxResults": 10,
        "priceTo": 20000,
        "propertyType": "flat",
        "transactionType": "rent"
    }

    sreality_input = {
        "location": "Praha",
        "max_pages": 10,
        "results_wanted": limit,
        "startUrl": "https://www.sreality.cz/hledani/pronajem/"
    }


    print(f"Running scraper for provider {provider}")
    print(PROVIDERS[provider])

    # Select the right actor and input
    actor_id = PROVIDERS[provider]["actor_id"]
    actor_input = sreality_input if provider == "sreality" else bezrealitky_input

    run = client.actor(actor_id).call(run_input= actor_input)
    print(f"✅ Actor run finished! Status: {run.status}")

    print(run)
    # dataset = client.dataset(run["defaultDatasetId"])

    # Fetch results from dataset
    # dataset = client.dataset(run["defaultDatasetId"])
    dataset = client.dataset(run.default_dataset_id)
    raw_items = list(dataset.iterate_items())
    print(f"📦 Downloaded {len(raw_items)} items from Apify.")

    return raw_items



if __name__ == "__main__":
        items = fetching_apify("bezrealitky", limit=5)
        index = 0
        for item in items:
            print(json.dumps(items[index], indent=2, ensure_ascii=False))
            index += 1