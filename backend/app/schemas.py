from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl


class BezrealitkyListing(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    url: HttpUrl
    title: str = Field(min_length=1)
    transactionType: Literal["rent", "sale"]
    propertyType: str
    disposition: str | None = None
    address: str | None = None
    city: str | None = None
    region: str | None = None
    price: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    charges: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    area: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    landArea: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    energyRating: str | None = None
    images: list[HttpUrl] = Field(default_factory=list)
    mainImage: HttpUrl | None = None
    isNew: bool = False
    isReserved: bool = False
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    sellerName: str | None = None
    sellerType: str | None = None
    listingSource: str | None = None
    scrapedAt: AwareDatetime


class Listing(BaseModel):
    source: Literal["bezrealitky"]
    external_id: str
    url: str
    title: str
    transaction_type: Literal["rent", "sale"]
    property_type: str
    disposition: str | None
    address: str | None
    city: str | None
    region: str | None
    price: float | None
    currency: str
    charges: float | None
    area: float | None
    land_area: float | None
    latitude: float | None
    longitude: float | None
    energy_rating: str | None
    images: list[str]
    main_image: str | None
    is_new: bool
    is_reserved: bool
    tags: list[str]
    description: str | None
    seller_name: str | None
    seller_type: str | None
    listing_source: str | None
    scraped_at: AwareDatetime
    is_deleted: bool = False
    deleted_at: AwareDatetime | None = None
    availability_checked_at: AwareDatetime | None = None
    deletion_reason: str | None = None


def normalize(item: BezrealitkyListing) -> Listing:
    return Listing(
        source="bezrealitky", external_id=item.id, url=str(item.url),
        title=item.title, transaction_type=item.transactionType,
        property_type=item.propertyType, disposition=item.disposition,
        address=item.address, city=item.city, region=item.region,
        price=item.price, currency=item.currency, charges=item.charges,
        area=item.area, land_area=item.landArea, latitude=item.latitude,
        longitude=item.longitude, energy_rating=item.energyRating,
        images=[str(url) for url in item.images],
        main_image=str(item.mainImage) if item.mainImage else None,
        is_new=item.isNew, is_reserved=item.isReserved, tags=item.tags,
        description=item.description, seller_name=item.sellerName,
        seller_type=item.sellerType, listing_source=item.listingSource,
        scraped_at=item.scrapedAt,
    )
