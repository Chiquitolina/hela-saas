from dataclasses import dataclass, asdict
from typing import Any, Optional

CATEGORY_CODES = {
    "FAMILIARES":"FAM", "TENTACIONES":"TEN", "POSTRES":"POS",
    "TORTAS":"TOR", "BOMBONES":"BOM", "PALITOS":"PAL",
    "LINEAS_ESPECIALES":"ESP", "FRIZZIO":"FRZ",
}
CATEGORY_SUBCATEGORIES = {
    "LINEAS_ESPECIALES":["SIN_AZUCAR","VEGANOS","YOGURES_HELADOS"],
    "FRIZZIO":["PIZZAS","EMPANADAS","BASTONCITOS_MOZZARELLA","PECHUGUITAS_POLLO"],
}
PACKAGING_PACK_BOXES_UNITS="PACK_CAJAS_UNIDADES"
PACKAGING_PACK_UNITS="PACK_UNIDADES"
PACKAGING_BOX_UNITS="CAJA_UNIDADES"
PACKAGING_MODES=[PACKAGING_PACK_BOXES_UNITS,PACKAGING_PACK_UNITS,PACKAGING_BOX_UNITS]

def _clean(v):
    if v is None:return ""
    return str(v).strip().upper().replace(" ","_")
def _ostr(v):
    x=_clean(v); return x or None
def _oint(v):
    if v is None:return None
    try:
        if v!=v:return None
    except:pass
    try:return int(float(v))
    except:return None

@dataclass
class CameraProduct:
    product_stock_id:str
    product_code:Optional[str]
    categoria:str
    subcategoria:Optional[str]
    producto:str
    packaging_mode:str
    cantidad_packs:Optional[int]
    cantidad_cajas:Optional[int]
    cajas_por_pack:Optional[int]
    unidades_por_pack:Optional[int]
    unidades_por_caja:Optional[int]
    total_cajas:Optional[int]
    total_unidades:int
    created_at:str
    updated_at:str
    active:bool

    @classmethod
    def create(cls,*,product_stock_id,product_code,categoria,subcategoria,producto,packaging_mode,
               cantidad_packs,cantidad_cajas,cajas_por_pack,unidades_por_pack,unidades_por_caja,timestamp):
        categoria=_clean(categoria); subcategoria=_ostr(subcategoria); producto=_clean(producto); packaging_mode=_clean(packaging_mode)
        if categoria not in CATEGORY_CODES: raise ValueError(f"Categoría inválida: {categoria}")
        allowed=CATEGORY_SUBCATEGORIES.get(categoria)
        if allowed:
            if not subcategoria or subcategoria not in allowed: raise ValueError(f"Subcategoría inválida para {categoria}")
        else: subcategoria=None
        if not producto: raise ValueError("El producto no puede estar vacío.")
        if packaging_mode not in PACKAGING_MODES: raise ValueError("Modo de empaque inválido.")
        cantidad_packs=_oint(cantidad_packs); cantidad_cajas=_oint(cantidad_cajas); cajas_por_pack=_oint(cajas_por_pack)
        unidades_por_pack=_oint(unidades_por_pack); unidades_por_caja=_oint(unidades_por_caja); total_cajas=None
        if packaging_mode==PACKAGING_PACK_BOXES_UNITS:
            if not cantidad_packs or not cajas_por_pack or not unidades_por_caja: raise ValueError("Completá packs, cajas por pack y unidades por caja.")
            total_cajas=cantidad_packs*cajas_por_pack; total_unidades=total_cajas*unidades_por_caja
            cantidad_cajas=None; unidades_por_pack=None
        elif packaging_mode==PACKAGING_PACK_UNITS:
            if not cantidad_packs or not unidades_por_pack: raise ValueError("Completá packs y unidades por pack.")
            total_unidades=cantidad_packs*unidades_por_pack
            cantidad_cajas=None; cajas_por_pack=None; unidades_por_caja=None
        else:
            if not cantidad_cajas or not unidades_por_caja: raise ValueError("Completá cajas y unidades por caja.")
            total_cajas=cantidad_cajas; total_unidades=cantidad_cajas*unidades_por_caja
            cantidad_packs=None; cajas_por_pack=None; unidades_por_pack=None
        return cls(str(product_stock_id),_ostr(product_code),categoria,subcategoria,producto,packaging_mode,
                   cantidad_packs,cantidad_cajas,cajas_por_pack,unidades_por_pack,unidades_por_caja,total_cajas,int(total_unidades),timestamp,timestamp,True)

    @classmethod
    def from_row(cls,row):
        g=row.get
        active=str(g('active',True)).strip().lower() in {'true','1','yes'}
        return cls(str(g('product_stock_id','')),_ostr(g('product_code')), _clean(g('categoria','')), _ostr(g('subcategoria')),
                   _clean(g('producto','')), _clean(g('packaging_mode',PACKAGING_PACK_UNITS)), _oint(g('cantidad_packs')),
                   _oint(g('cantidad_cajas')), _oint(g('cajas_por_pack')), _oint(g('unidades_por_pack')), _oint(g('unidades_por_caja')),
                   _oint(g('total_cajas')), _oint(g('total_unidades')) or 0, str(g('created_at','')), str(g('updated_at','')), active)
    def to_row(self): return asdict(self)
    def annul_updates(self,*,timestamp):
        if not self.active: raise ValueError("El stock del producto ya no está activo.")
        return {'updated_at':timestamp,'active':False}
