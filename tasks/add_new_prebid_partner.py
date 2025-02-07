#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import os
import sys
from builtins import input
from pprint import pprint

from colorama import init

import settings
import dfp.associate_line_items_and_creatives
import dfp.create_custom_targeting
import dfp.create_creatives
import dfp.create_line_items
import dfp.create_orders
import dfp.get_ad_units
import dfp.get_advertisers
import dfp.get_custom_targeting
import dfp.get_placements
import dfp.get_users
from dfp.exceptions import (
  BadSettingException,
  MissingSettingException
)
from tasks.price_utils import (
  get_prices_array,
  get_prices_summary_string,
  micro_amount_to_num,
  num_to_str,
)

# Colorama for cross-platform support for colored logging.
# https://github.com/kmjennison/dfp-prebid-setup/issues/9
init()

# Configure logging.
if 'DISABLE_LOGGING' in os.environ and os.environ['DISABLE_LOGGING'] == 'true':
  logging.disable(logging.CRITICAL)
  logging.getLogger('googleads').setLevel(logging.CRITICAL)
  logging.getLogger('oauth2client').setLevel(logging.CRITICAL)
else:
  FORMAT = '%(message)s'
  logging.basicConfig(stream=sys.stdout, level=logging.INFO, format=FORMAT)
  logging.getLogger('googleads').setLevel(logging.ERROR)
  logging.getLogger('oauth2client').setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


def setup_partner(user_email, advertiser_name, order_name, placements, ad_units, sizes, bidder_code, prices,
                  num_creatives, currency_code, line_item_format, name_prefix, video_ad_type=False, redirect_url=''):
  """
  Call all necessary DFP tasks for a new Prebid partner setup.
  """

  # Get the user.
  user_id = dfp.get_users.get_user_id_by_email(user_email)

  # Get the placement IDs.
  placement_ids = dfp.get_placements.get_placement_ids_by_name(placements)

  # Get the ad unit IDs.
  ad_unit_ids = dfp.get_ad_units.get_ad_unit_ids_by_name(ad_units)

  # Get (or potentially create) the advertiser.
  advertiser_id = dfp.get_advertisers.get_advertiser_id_by_name(
    advertiser_name)

  # Create the order.
  order_id = dfp.create_orders.create_order(order_name, advertiser_id, user_id)

  # Create creatives.
  creative_configs = dfp.create_creatives.create_duplicate_creative_configs(
      name_prefix, order_name, advertiser_id, num_creatives, video_ad_type, redirect_url)
  creative_ids = dfp.create_creatives.create_creatives(creative_configs)

  # Get DFP key IDs for line item targeting.
  if bidder_code is not None: 
    hb_bidder_key_id = get_or_create_dfp_targeting_key('hb_bidder')
  else:
    hb_bidder_key_id = None
  
  hb_pb_key_id = get_or_create_dfp_targeting_key('hb_pb')

  # Instantiate DFP targeting value ID getters for the targeting keys.
  if bidder_code is not None:
    HBBidderValueGetter = DFPValueIdGetter('hb_bidder')
  else:
    HBBidderValueGetter = None
  
  HBPBValueGetter = DFPValueIdGetter('hb_pb')

  # Create line items.
  line_items_config = create_line_item_configs(prices, order_id, placement_ids, ad_unit_ids, bidder_code, sizes,
                                               hb_bidder_key_id, hb_pb_key_id, currency_code, line_item_format,
                                               HBBidderValueGetter, HBPBValueGetter, video_ad_type, name_prefix)
  logger.info("Creating line items...")
  line_item_ids = dfp.create_line_items.create_line_items(line_items_config)

  # Associate creatives with line items.
  dfp.associate_line_items_and_creatives.make_licas(line_item_ids,
    creative_ids, size_overrides=sizes)

  logger.info("""

    Done! Please review your order, line items, and creatives to
    make sure they are correct. Then, approve the order in DFP.

    Happy bidding!

  """)

class DFPValueIdGetter(object):
  """
  A class to bulk fetch DFP values by key and then create new values as needed.
  """

  def __init__(self, key_name, *args, **kwargs):
    """
    Args:
      key_name (str): the name of the DFP key
    """
    self.key_name = key_name
    self.key_id = dfp.get_custom_targeting.get_key_id_by_name(key_name)
    self.existing_values = dfp.get_custom_targeting.get_targeting_by_key_name(
      key_name)
    super(DFPValueIdGetter, self).__init__(*args, **kwargs)

  def _get_value_id_from_cache(self, value_name):
    val_id = None
    for value_obj in self.existing_values:
      if value_obj['name'] == value_name:
        val_id = value_obj['id']
        break
    return val_id

  def _create_value_and_return_id(self, value_name):
    return dfp.create_custom_targeting.create_targeting_value(value_name,
      self.key_id)

  def get_value_id(self, value_name):
    """
    Get the DFP custom value ID, or create it if it doesn't exist.

    Args:
      value_name (str): the name of the DFP value
    Returns:
      an integer: the ID of the DFP value
    """
    val_id = self._get_value_id_from_cache(value_name)
    if not val_id:
      val_id = self._create_value_and_return_id(value_name)
    return val_id


def get_or_create_dfp_targeting_key(name):
  """
  Get or create a custom targeting key by name.

  Args:
    name (str)
  Returns:
    an integer: the ID of the targeting key
  """
  key_id = dfp.get_custom_targeting.get_key_id_by_name(name)
  if key_id is None:
    key_id = dfp.create_custom_targeting.create_targeting_key(name)
  return key_id

def create_line_item_configs(prices, order_id, placement_ids, ad_unit_ids, bidder_code, sizes, hb_bidder_key_id,
                             hb_pb_key_id, currency_code, line_item_format, HBBidderValueGetter, HBPBValueGetter,
                             video_ad_type, name_prefix):
  """
  Create a line item config for each price bucket.

  Args:
    prices (array)
    order_id (int)
    placement_ids (arr)
    ad_unit_ids (arr)
    bidder_code (str)
    hb_bidder_key_id (int)
    hb_pb_key_id (int)
    currency_code (str)
    line_item_format (str)
    HBBidderValueGetter (DFPValueIdGetter)
    HBPBValueGetter (DFPValueIdGetter)
    video_ad_type (bool)
	name_prefix (str)
  Returns:
    an array of objects: the array of DFP line item configurations
  """

  # The DFP targeting value ID for this `hb_bidder` code.
  if HBBidderValueGetter is not None:
    hb_bidder_value_id = HBBidderValueGetter.get_value_id(bidder_code)
  else:
    hb_bidder_value_id = None

  line_items_config = []
  for price in prices:

    price_str = num_to_str(micro_amount_to_num(price))

    # Autogenerate the line item name. - hack to help w/ bidder_code = None
    line_item_name = line_item_format.format(
      bidder_code=name_prefix,
      price=price_str
    )

    # The DFP targeting value ID for this `hb_pb` price value.
    hb_pb_value_id = HBPBValueGetter.get_value_id(price_str)

    config = dfp.create_line_items.create_line_item_config(name=line_item_name, order_id=order_id,
                                                           placement_ids=placement_ids, ad_unit_ids=ad_unit_ids,
                                                           cpm_micro_amount=price, sizes=sizes,
                                                           hb_bidder_key_id=hb_bidder_key_id, hb_pb_key_id=hb_pb_key_id,
                                                           hb_bidder_value_id=hb_bidder_value_id,
                                                           hb_pb_value_id=hb_pb_value_id, currency_code=currency_code,
                                                           video_ad_type=video_ad_type)

    line_items_config.append(config)

  return line_items_config

def check_price_buckets_validity(price_buckets):
  """
  Validate that the price_buckets object contains all required keys and the
  values are the expected types.

  Args:
    price_buckets (object)
  Returns:
    None
  """

  try:
    pb_precision = price_buckets['precision']
    pb_min = price_buckets['min']
    pb_max = price_buckets['max']
    pb_increment = price_buckets['increment']
  except KeyError:
    raise BadSettingException('The setting "PREBID_PRICE_BUCKETS" '
      'must contain keys "precision", "min", "max", and "increment".')

  if not (isinstance(pb_precision, int) or isinstance(pb_precision, float)):
    raise BadSettingException('The "precision" key in "PREBID_PRICE_BUCKETS" '
      'must be a number.')

  if not (isinstance(pb_min, int) or isinstance(pb_min, float)):
    raise BadSettingException('The "min" key in "PREBID_PRICE_BUCKETS" '
      'must be a number.')

  if not (isinstance(pb_max, int) or isinstance(pb_max, float)):
    raise BadSettingException('The "max" key in "PREBID_PRICE_BUCKETS" '
      'must be a number.')

  if not (isinstance(pb_increment, int) or isinstance(pb_increment, float)):
    raise BadSettingException('The "increment" key in "PREBID_PRICE_BUCKETS" '
      'must be a number.')

class color:
   PURPLE = '\033[95m'
   CYAN = '\033[96m'
   DARKCYAN = '\033[36m'
   BLUE = '\033[94m'
   GREEN = '\033[92m'
   YELLOW = '\033[93m'
   RED = '\033[91m'
   BOLD = '\033[1m'
   UNDERLINE = '\033[4m'
   END = '\033[0m'

def main():
  """
  Validate the settings and ask for confirmation from the user. Then,
  start all necessary DFP tasks.
  """

  user_email = getattr(settings, 'DFP_USER_EMAIL_ADDRESS', None)
  if user_email is None:
    raise MissingSettingException('DFP_USER_EMAIL_ADDRESS')

  advertiser_name = getattr(settings, 'DFP_ADVERTISER_NAME', None)
  if advertiser_name is None:
    raise MissingSettingException('DFP_ADVERTISER_NAME')

  order_name = getattr(settings, 'DFP_ORDER_NAME', None)
  if order_name is None:
    raise MissingSettingException('DFP_ORDER_NAME')

  placements = getattr(settings, 'DFP_TARGETED_PLACEMENT_NAMES', None)
  ad_units = getattr(settings, 'DFP_TARGETED_AD_UNIT_NAMES', None)

  video_ad_type = getattr(settings, 'DFP_VIDEO_AD_TYPE', False)
  vast_redirect_url = getattr(settings, 'DFP_VAST_REDIRECT_URL', '')
  if video_ad_type is True and len(vast_redirect_url) < 1:
    raise BadSettingException('When setting "DFP_VIDEO_AD_TYPE" to "True", please also set "DFP_VAST_REDIRECT_URL".')
  if ad_units is None and placements is None:
    raise MissingSettingException('DFP_TARGETED_PLACEMENT_NAMES or DFP_TARGETED_AD_UNIT_NAMES')
  elif (placements is None or len(placements) < 1) and (ad_units is None or len(ad_units) < 1):
    raise BadSettingException('The setting "DFP_TARGETED_PLACEMENT_NAMES" or "DFP_TARGETED_AD_UNIT_NAMES" '
      'must contain at least one DFP placement or ad unit.')

  sizes = getattr(settings, 'DFP_PLACEMENT_SIZES', None)
  if sizes is None:
    raise MissingSettingException('DFP_PLACEMENT_SIZES')
  elif len(sizes) < 1:
    raise BadSettingException('The setting "DFP_PLACEMENT_SIZES" '
      'must contain at least one size object.')

  currency_code = getattr(settings, 'DFP_CURRENCY_CODE', 'USD')

  line_item_format = getattr(settings, 'DFP_LINE_ITEM_FORMAT', u'{bidder_code}: HB ${price}')

  # How many creatives to attach to each line item. We need at least one
  # creative per ad unit on a page. See:
  # https://github.com/kmjennison/dfp-prebid-setup/issues/13
  num_creatives = (
    getattr(settings, 'DFP_NUM_CREATIVES_PER_LINE_ITEM', None) or
    len(placements) + len(ad_units)
  )

  bidder_code = getattr(settings, 'PREBID_BIDDER_CODE', None)

  name_prefix = getattr(settings, 'DFP_NAME_PREFIX', None)

  price_buckets = getattr(settings, 'PREBID_PRICE_BUCKETS', None)
  if price_buckets is None:
    raise MissingSettingException('PREBID_PRICE_BUCKETS')

  check_price_buckets_validity(price_buckets)

  prices = get_prices_array(price_buckets)
  prices_summary = get_prices_summary_string(prices,
    price_buckets['precision'])

  logger.info(
    u"""

    Going to create {name_start_format}{num_line_items}{format_end} new line items.
      {name_start_format}Order{format_end}: {value_start_format}{order_name}{format_end}
      {name_start_format}Advertiser{format_end}: {value_start_format}{advertiser}{format_end}

    Line items will have targeting:
      {name_start_format}hb_pb{format_end} = {value_start_format}{prices_summary}{format_end}
      {name_start_format}hb_bidder{format_end} = {value_start_format}{bidder_code}{format_end}
      {name_start_format}placements{format_end} = {value_start_format}{placements}{format_end}
      {name_start_format}ad units{format_end} = {value_start_format}{ad_units}{format_end}

    """.format(
      num_line_items = len(prices),
      order_name=order_name,
      advertiser=advertiser_name,
      user_email=user_email,
      prices_summary=prices_summary,
      bidder_code=bidder_code,
      placements=placements,
      ad_units=ad_units,
      sizes=sizes,
      name_start_format=color.BOLD,
      format_end=color.END,
      value_start_format=color.BLUE,
    ))

  if video_ad_type:
    logger.info(
    u"""    Line items will have VAST redirect creatives with redirect URL:
      {value_start_format}{redirect_url}{format_end}
    """.format(
      redirect_url=vast_redirect_url,
      value_start_format=color.BLUE,
      format_end=color.END,
    ))
  else:
    logger.info(
    u"""    Line items will have third party creatives based on snippet.html content.
    """)
  ok = input('Is this correct? (y/n)\n')

  if ok != 'y':
    logger.info('Exiting.')
    return

  setup_partner(user_email, advertiser_name, order_name, placements, ad_units, sizes, bidder_code, prices, num_creatives, currency_code, line_item_format, name_prefix, video_ad_type, vast_redirect_url)

if __name__ == '__main__':
  main()