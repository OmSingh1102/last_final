REASON_CODES = {
    "10.4": {
        "title": "Other Fraud, Card-Absent Environment",
        "network_codes": {
            "Visa": "10.4",
            "Mastercard": "4837 - No Cardholder Authorization",
            "Amex": "F29 Card Not Present",
            "Discover": "UA02 - Fraud – Card Not Present Transaction"
        },
        "definition": "This chargeback reason code is used when a cardholder disputes a transaction conducted in a card-not-present (CNP) environment, claiming they did not authorize or participate in the transaction.",
        "scenarios": ["True Fraud", "Friendly Fraud (Chargeback Fraud)"],
        "merchant_challenge": "Prove the transaction was legitimate and the cardholder made the purchase or is responsible for it. Particularly difficult in card-absent environments.",
        "defense_goals": [
            "The transaction was genuinely authorized by the cardholder",
            "The cardholder received the goods or services",
            "The chargeback is invalid due to friendly fraud",
            "A refund was already issued for the disputed amount"
        ],
        "supporting_docs_general": [
            {"category": "Proof of Cardholder Authentication", "evidences": ["AVS match and CVV2 match", "Visa Secure / 3-D Secure authentication"]},
            {"category": "Proof of Delivery and Service", "evidences": ["Shipping and delivery confirmation", "Proof of usage", "Travel and entertainment (T&E)"]},
            {"category": "Transaction and Account History", "evidences": ["Matching IP addresses", "Consistent account details", "Purchase history", "Account log-in details"]},
            {"category": "Merchant-Cardholder Communications", "evidences": ["Copies of all correspondence (emails, chat logs, phone records)"]},
            {"category": "For Recurring Transactions", "evidences": ["Transaction History", "Service Usage", "Proof of consent signed documents"]}
        ],
        "supporting_docs_platform": [
            {"category": "Proof of Cardholder Authentication", "evidences": ["Visa Secure / 3-D Secure authentication"]},
            {"category": "Proof of Delivery and Service", "evidences": ["Shipping and delivery confirmation", "Proof of usage"]},
            {"category": "Transaction and Account History", "evidences": ["Matching IP addresses and Geo Location", "Consistent account details", "Account History, Binding History and Purchase history", "Account log-in details", "Undisputed transactions"]},
            {"category": "Merchant-Cardholder Communications", "evidences": ["Copies of all correspondence (emails, chat logs, phone records)"]},
            {"category": "For Recurring Transactions", "evidences": ["Subscription"]}
        ],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "11.3": {
        "title": "No Authorization",
        "network_codes": {
            "Visa": "11.3",
            "Mastercard": "NA - No Authorization",
            "Amex": "A02 - No Valid Authorization",
            "Discover": "UA06 - Fraud – Chip and PIN"
        },
        "definition": "This chargeback reason code is filed when a transaction is processed without a valid authorization. Obtaining authorization from the card issuer is a fundamental step in the payment process.",
        "scenarios": ["True Fraud", "Friendly Fraud (Chargeback Fraud)", "Merchant Error"],
        "merchant_challenge": "Prove that the card issuer's claim of 'no authorization' is false. Show that a valid authorization was indeed obtained.",
        "defense_goals": [
            "The transaction was genuinely authorized by the cardholder",
            "The cardholder received the goods or services",
            "A refund was already issued for the disputed amount",
            "Written communication where cardholder explicitly states they no longer wish to dispute"
        ],
        "supporting_docs_general": [
            {"category": "Proof of Valid Authorization", "evidences": ["AVS match and CVV match", "Proof of Auth - Success & Failure", "Visa Secure / 3-D Secure authentication"]},
            {"category": "Proof of Delivery and Service", "evidences": ["Shipping and delivery confirmation", "Proof of usage", "Travel and entertainment (T&E)"]},
            {"category": "Transaction and Account History", "evidences": ["Matching IP addresses", "Consistent account details", "Purchase history", "Account log-in details"]},
            {"category": "Merchant-Cardholder Communications", "evidences": ["Copies of all correspondence (emails, chat logs, phone records)"]},
            {"category": "For Recurring Transactions", "evidences": ["Transaction History", "Service Usage", "Proof of consent signed documents"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "12.5": {
        "title": "Incorrect Amount",
        "network_codes": {
            "Visa": "12.5",
            "Mastercard": "4831 - Transaction Amount Differs",
            "Amex": "AW - Altered Amount",
            "Discover": "P05 - Incorrect Charge Amount"
        },
        "definition": "The cardholder claims the amount charged is different from the amount they expected or agreed to pay. The discrepancy can be a clerical error, calculation mistake, or unauthorized change.",
        "scenarios": ["Merchant Error", "Data Entry Error", "Calculation Error", "Unauthorized Adjustments", "Friendly Fraud"],
        "merchant_challenge": "Provide compelling evidence that proves the amount charged was correct and the cardholder consented to it.",
        "defense_goals": [
            "The transaction matches the agreed amount",
            "The cardholder received the goods or services",
            "A refund was already issued for the disputed amount",
            "The burden of proof is on the merchant"
        ],
        "supporting_docs_general": [
            {"category": "Transaction and Account History", "evidences": ["Consistent account details", "Purchase history", "Account log-in details", "Signed Sales Receipt", "Proof of Refund"]},
            {"category": "Merchant-Cardholder Communications", "evidences": ["Copies of all correspondence (emails, chat logs, phone records)"]},
            {"category": "For Recurring Transactions", "evidences": ["Transaction History", "Service Usage", "Proof of consent signed documents"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "12.6.1": {
        "title": "Duplicate Processing",
        "network_codes": {
            "Visa": "12.6.1",
            "Mastercard": "DP/4534 - Duplicate Processing",
            "Amex": "P08 - Duplicate Charge",
            "Discover": "4834 - Point of Interaction"
        },
        "definition": "The cardholder claims they were charged more than once for the same transaction due to a merchant system error or manual error submitting the transaction multiple times.",
        "scenarios": ["Merchant Error", "Friendly Fraud"],
        "merchant_challenge": "Prove the two charges were not duplicates but for two separate, distinct transactions with valid and separate purchases.",
        "defense_goals": [
            "The transaction matches the agreed amount",
            "The cardholder received the goods or services",
            "A refund was already issued for the disputed amount",
            "The burden of proof is on the merchant"
        ],
        "supporting_docs_general": [
            {"category": "Transaction and Account History", "evidences": ["Consistent account details", "Purchase history", "Account log-in details", "Signed Sales Receipt", "Proof of Refund"]},
            {"category": "Merchant-Cardholder Communications", "evidences": ["Copies of all correspondence (emails, chat logs, phone records)"]},
            {"category": "For Recurring Transactions", "evidences": ["Transaction History", "Service Usage", "Proof of consent signed documents"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "12.6.2": {
        "title": "Paid by Other Means",
        "network_codes": {
            "Visa": "12.6.2",
            "Mastercard": "4853-5 (Credit not processed)",
            "Amex": "C14 - Paid by Other Means",
            "Discover": "4834 - Paid by Other Means"
        },
        "definition": "The cardholder claims they paid for goods/services using an alternative payment method (cash, check, another card, store credit) and were charged on their card in error.",
        "scenarios": ["Merchant Error", "Friendly Fraud"],
        "merchant_challenge": "Prove the credit card was the only form of payment used for the transaction.",
        "defense_goals": [
            "The transaction matches the agreed amount",
            "The cardholder received the goods or services",
            "A refund was already issued for the disputed amount",
            "The burden of proof is on the merchant"
        ],
        "supporting_docs_general": [
            {"category": "Transaction and Account History", "evidences": ["Consistent account details", "Purchase history", "Account log-in details", "Signed Sales Receipt", "Proof of Refund"]},
            {"category": "Merchant-Cardholder Communications", "evidences": ["Copies of all correspondence (emails, chat logs, phone records)"]},
            {"category": "For Recurring Transactions", "evidences": ["Transaction History", "Service Usage", "Proof of consent signed documents"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "13.1": {
        "title": "Merchandise/Service Not Received",
        "network_codes": {
            "Visa": "13.1",
            "Mastercard": "4853",
            "Amex": "C08",
            "Discover": ""
        },
        "definition": "The cardholder is disputing a transaction by claiming they never received the merchandise or services they purchased.",
        "scenarios": ["Merchant Error", "Friendly Fraud"],
        "merchant_challenge": "Provide compelling evidence of delivery including tracking, signed receipts, photos, transaction records, customer communication, and for services, proof of rendering.",
        "defense_goals": [
            "Proof of Delivery: shipping carrier tracking, signature confirmation, AVS-matched delivery address",
            "Proof of Service: redeemed coupons, confirmation emails, login records",
            "Communication with the Cardholder showing acknowledgment",
            "Proof of a Refund if already issued",
            "Evidence of undisputed purchases from the same device/card"
        ],
        "supporting_docs_general": [
            {"category": "Tracking and Delivery", "evidences": ["Tracking numbers showing delivery date, time, correct address", "Signature confirmation for high-value items", "Customer address matches transaction address"]},
            {"category": "Service Confirmation", "evidences": ["Signed work orders / service reports / contracts", "Usage logs or records (login history, activity)", "Event ticket scanning or redemption records"]},
            {"category": "Correspondence", "evidences": ["Emails, chat logs, communications acknowledging receipt or delays"]},
            {"category": "Transaction Details", "evidences": ["Original receipt with date, amount, item/service description"]},
            {"category": "Terms and Conditions", "evidences": ["Proof customer agreed to terms of service or return policy"]}
        ],
        "supporting_docs_platform": [
            {"category": "Tracking and Delivery", "evidences": ["Marketplace shipping tracking with carrier confirmation", "Delivery photos from carrier"]},
            {"category": "Account Evidence", "evidences": ["Account activity showing order was placed by account holder", "IP address and geolocation matching", "Undisputed transactions from same account"]},
            {"category": "Communications", "evidences": ["In-app messages or customer service chat logs"]}
        ],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "13.2": {
        "title": "Cancelled Recurring Transaction",
        "network_codes": {
            "Visa": "13.2",
            "Mastercard": "4853",
            "Amex": "C28",
            "Discover": ""
        },
        "definition": "The cardholder disputes a recurring transaction, claiming the merchant processed the payment after they had already requested to cancel the service, subscription, or recurring billing agreement.",
        "scenarios": ["Merchant Error", "Friendly Fraud"],
        "merchant_challenge": "Prove the subscription was active, the cancellation policy was followed, and/or the service was used after the charge.",
        "defense_goals": [
            "Proof of Active Subscription showing it was active at time of charge",
            "Cancellation Policy showing the charge aligns with its terms",
            "Proof of Service Usage after the charge",
            "Proof of a Refund if one was processed"
        ],
        "supporting_docs_general": [
            {"category": "Subscription Evidence", "evidences": ["Original recurring billing agreement", "Cancellation policy with required notice period", "Customer account logs proving cancellation after billing date"]},
            {"category": "Usage Proof", "evidences": ["Proof of continued service use after the charge", "Login records, activity logs"]},
            {"category": "Refund Evidence", "evidences": ["Payment processor records of refund (date, amount, ID)", "Customer communication confirming refund"]},
            {"category": "Transaction Documentation", "evidences": ["Transaction receipt/invoice", "Terms and conditions"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "13.3": {
        "title": "Not as Described or Defective Merchandise/Services",
        "network_codes": {
            "Visa": "13.3",
            "Mastercard": "C31",
            "Amex": "RM",
            "Discover": ""
        },
        "definition": "The cardholder claims goods/services received did not match the description, were damaged or defective, or were of lower quality than expected.",
        "scenarios": ["Merchant Error", "Friendly Fraud"],
        "merchant_challenge": "Prove quality and disprove defect claims. These are subjective chargebacks requiring extensive evidence beyond delivery proof.",
        "defense_goals": [
            "Prove product quality with pre-shipping documentation",
            "Show accurate product description matched what was delivered",
            "Provide evidence of friendly fraud (continued use after claimed issue)",
            "Show refund/replacement was already offered"
        ],
        "supporting_docs_general": [
            {"category": "Product Description Proof", "evidences": ["Screenshots of product page", "Original sales invoice", "Terms and conditions the cardholder agreed to"]},
            {"category": "Delivery/Service Proof", "evidences": ["Shipping carrier tracking information", "Usage logs, timestamps, signed contracts"]},
            {"category": "Customer Communication", "evidences": ["All emails, chat transcripts showing cardholder accepted item", "Records showing cardholder didn't follow return policy", "Attempted resolution correspondence"]},
            {"category": "Refund/Replacement Evidence", "evidences": ["Payment processor documentation of refund", "New tracking details for replacement item"]},
            {"category": "Friendly Fraud Evidence", "evidences": ["Past undisputed transactions from same cardholder", "Usage logs showing continued use after claimed issue"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "13.6": {
        "title": "Credit Not Processed",
        "network_codes": {
            "Visa": "13.6",
            "Mastercard": "4860",
            "Amex": "C02",
            "Discover": "RN2"
        },
        "definition": "The cardholder claims the merchant promised them a refund or credit but failed to process it.",
        "scenarios": ["Merchant Error", "Friendly Fraud"],
        "merchant_challenge": "Prove the refund was already issued, or that the customer is not eligible for a refund per the return/cancellation policy.",
        "defense_goals": [
            "Proof of Credit already issued (date, amount, transaction ID)",
            "Customer Communication confirming the credit was processed",
            "Return/Cancellation Policy showing conditions for refund",
            "Proof of Policy Acknowledgment at time of purchase"
        ],
        "supporting_docs_general": [
            {"category": "Proof of Credit", "evidences": ["Payment processor record showing refund date, amount, transaction ID", "Customer communication confirming credit was processed", "Timestamp showing credit processed before chargeback date"]},
            {"category": "Policy Documentation", "evidences": ["Return/Cancellation policy with refund conditions", "Proof customer agreed to policy at purchase (checkout page screenshot)"]},
            {"category": "Communication Records", "evidences": ["Correspondence explaining why customer was not eligible for refund"]},
            {"category": "Void Documentation", "evidences": ["POS/payment processor documentation of voided transaction", "Transaction logs from original transaction to void"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    },
    "13.7": {
        "title": "Cancelled Merchandise/Services",
        "network_codes": {
            "Visa": "13.7",
            "Mastercard": "4853",
            "Amex": "C05",
            "Discover": ""
        },
        "definition": "The cardholder claims to have returned merchandise or canceled a service, but the merchant has not issued the promised credit or refund.",
        "scenarios": ["Merchant Error", "Friendly Fraud"],
        "merchant_challenge": "Prove that the customer is not entitled to a refund, or that a refund was already issued.",
        "defense_goals": [
            "Payment processor documentation showing refund was successful",
            "Customer communication confirming refund",
            "Cancellation/return policy outlining refund conditions",
            "Proof customer was aware of and agreed to policy"
        ],
        "supporting_docs_general": [
            {"category": "Return/Cancellation Policy", "evidences": ["Copy of mutually agreed-upon return/cancellation policy"]},
            {"category": "Proof of Refund", "evidences": ["Payment processor documentation with date, amount, transaction ID"]},
            {"category": "Shipping/Tracking", "evidences": ["Tracking showing no return delivery received", "Record showing no return shipment was initiated"]},
            {"category": "Communication with Cardholder", "evidences": ["Explanation of policy", "Attempts to resolve issue directly", "Cardholder acknowledgment of policy non-compliance"]},
            {"category": "Proof of Use", "evidences": ["Logs showing continued use after claimed cancellation"]},
            {"category": "Resolution Confirmation", "evidences": ["Signed letter or email from cardholder confirming dispute withdrawal"]}
        ],
        "supporting_docs_platform": [],
        "portals": ["Merchant processor portal", "CRM", "Payment Gateway", "Shipment portals"]
    }
}

# ─── Scenario to Reason Code Mapping ──────────────────────────────────────────
SCENARIO_CATEGORIES = {
    "Fraud - Card Not Present (CNP)": {"reason_code": "10.4", "chargeback_category": "Fraud - CNP"},
    "Fraud - No Authorization": {"reason_code": "11.3", "chargeback_category": "Fraud - No Auth"},
    "Fraud - Merchant Liability (No 3DS)": {"reason_code": "10.4", "chargeback_category": "Fraud - Merchant Liable"},
    "Merchandise - Item Not Received": {"reason_code": "13.1", "chargeback_category": "Merchandise - Not Received"},
    "Merchandise - Item Defective": {"reason_code": "13.3", "chargeback_category": "Merchandise - Defective Item"},
    "Merchandise - Not as Described": {"reason_code": "13.3", "chargeback_category": "Merchandise - Not as Described"},
    "Processing - Duplicate Charge": {"reason_code": "12.6.1", "chargeback_category": "Processing - Duplicate"},
    "Processing - Incorrect Amount": {"reason_code": "12.5", "chargeback_category": "Processing - Incorrect Amount"},
    "Processing - Paid by Other Means": {"reason_code": "12.6.2", "chargeback_category": "Processing - Paid Other Means"},
    "Subscription - Cancelled Recurring": {"reason_code": "13.2", "chargeback_category": "Subscription - Cancelled"},
    "Refund - Credit Not Processed": {"reason_code": "13.6", "chargeback_category": "Refund - Credit Not Processed"},
    "Refund - Cancelled Merchandise": {"reason_code": "13.7", "chargeback_category": "Refund - Cancelled Merch"},
}


class ReasonCodeInterpreter:
    """Maps chargeback reason codes to defense strategies,
    network codes, and scenario categories."""

    @classmethod
    def interpret(cls, reason_code):
        """Look up a reason code and return its full defense strategy."""
        return REASON_CODES.get(reason_code, {})

    @classmethod
    def get_scenario_info(cls, scenario_name):
        """Map a scenario name to its reason code and category."""
        return SCENARIO_CATEGORIES.get(scenario_name, {})

    @classmethod
    def get_all_codes(cls):
        """Return the full reason code knowledge base."""
        return REASON_CODES


class ReasonCodeRulebook:
    """Rule set defining mandatory evidence by reason code."""

    RULES = {
        "10.4": [
            "Gateway receipt with auth code, AVS, CVV, 3DS, transaction ID",
            "CRM order confirmation and customer contact trace",
            "IP address and prior undisputed transaction evidence",
            "Terms and conditions acceptance snapshot",
        ],
        "11.3": [
            "Gateway authorization logs and transaction copy",
            "AVS/CVV/3DS verification evidence",
            "CRM order confirmation and communication proof",
            "Policy documents and checkout consent record",
        ],
        "12.5": [
            "Transaction amount audit trail",
            "Gateway capture/settlement references",
            "Customer communication and invoice details",
            "Policy disclosure and checkout snapshot",
        ],
        "12.6.1": [
            "Duplicate transaction comparison report",
            "Gateway auth/capture timestamps",
            "CRM communications clarifying purchase intent",
            "Refund or correction policy evidence",
        ],
        "12.6.2": [
            "Alternative payment verification records",
            "Gateway transaction copy and authorization proof",
            "CRM order and communication records",
            "Terms and refund policy artifacts",
        ],
        "13.1": [
            "Proof of delivery with tracking outcome",
            "CRM order confirmation and dispatch timeline",
            "Gateway receipt and IP/device evidence",
            "Terms and return policy documents",
        ],
        "13.2": [
            "Subscription lifecycle and cancellation logs",
            "Gateway recurring billing authorization proof",
            "Customer communication records",
            "Cancellation policy documentation",
        ],
        "13.3": [
            "Product description and fulfillment evidence",
            "CRM correspondence for dispute handling",
            "Gateway transaction and auth snapshot",
            "Return policy and checkout terms",
        ],
        "13.6": [
            "Refund/credit processing evidence",
            "CRM communication confirming credit status",
            "Gateway refund/void timeline",
            "Refund policy with customer acknowledgment",
        ],
        "13.7": [
            "Return cancellation eligibility records",
            "POD or non-return tracking evidence",
            "Gateway refund activity and transaction copy",
            "Terms and refund policy repository docs",
        ],
    }

    DEFAULT_RULE = [
        "Gateway transaction evidence and auth details",
        "CRM order confirmation data",
        "Fulfillment/POD tracking details",
        "Policy and terms repository documents",
    ]

    NETWORKS = ["Visa", "Mastercard", "Discover", "Diners Club", "Amex"]

    @classmethod
    def required_evidence(cls, reason_code):
        return cls.RULES.get(reason_code, cls.DEFAULT_RULE)

    @classmethod
    def network_reason_matrix(cls, reason_code):
        reason = REASON_CODES.get(reason_code, {})
        codes = reason.get("network_codes", {})
        discover_code = codes.get("Discover", "") or "Refer Discover bulletin"
        return {
            "Visa": codes.get("Visa", "Refer Visa core rules"),
            "Mastercard": codes.get("Mastercard", "Refer Mastercard chargeback guide"),
            "Discover": discover_code,
            "Diners Club": codes.get("Diners Club", discover_code),
            "Amex": codes.get("Amex", "Refer American Express reason guide"),
        }
