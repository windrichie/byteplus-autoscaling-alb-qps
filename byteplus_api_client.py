import json
import hashlib
import hmac
from urllib.parse import quote
from datetime import datetime, timezone
import requests
import logging
from typing import Dict, Any, Optional


class BytePlusAPIClient:
    """
    A modular BytePlus API client with request signing functionality.
    Supports different services and regions with proper authentication.
    """
    
    def __init__(self, access_key: str, secret_key: str, region: str = "ap-southeast-1"):
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.logger = logging.getLogger(__name__)
        
    def _norm_query(self, params: Dict[str, Any]) -> str:
        """Normalize query parameters for signing."""
        query = ""
        for key in sorted(params.keys()):
            if isinstance(params[key], list):
                for v in params[key]:
                    query += quote(key, safe='-_.~') + "=" + quote(v, safe='-_.~') + "&"
            else:
                query += quote(key, safe='-_.~') + "=" + quote(str(params[key]), safe='-_.~') + "&"
        return query.rstrip("&").replace("+", "%20")
    
    def _hmac_sha256(self, key: bytes, msg: str) -> bytes:
        """Generate HMAC-SHA256 hash."""
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
    
    def _hash_sha256(self, content: str) -> str:
        """Generate SHA256 hash."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    def _get_service_host(self, service: str) -> str:
        """Get the appropriate host for a service."""
        service_hosts = {
            # "ecs": f"open.{self.region}.byteplusapi.com",
            "auto_scaling": f"auto-scaling.{self.region}.byteplusapi.com",
            "volc_observe": f"volc-observe.{self.region}.byteplusapi.com",
            # "clb": f"open.{self.region}.byteplusapi.com",
            # "alb": f"open.{self.region}.byteplusapi.com"
        }
        return service_hosts.get(service, f"open.{self.region}.byteplusapi.com")
    
    def _sign_request(self, method: str, service: str, version: str, action: str, 
                     query_params: Dict[str, Any], body: str, 
                     additional_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Sign a request using BytePlus signature v4."""
        
        host = self._get_service_host(service)
        content_type = "application/json"
        date = datetime.now(timezone.utc)
        
        # Prepare request parameters
        request_param = {
            "body": body or "",
            "host": host,
            "path": "/",
            "method": method,
            "content_type": content_type,
            "date": date,
            "query": {"Action": action, "Version": version, **query_params},
        }
        
        x_date = request_param["date"].strftime("%Y%m%dT%H%M%SZ")
        short_x_date = x_date[:8]
        x_content_sha256 = self._hash_sha256(request_param["body"])
        
        # Prepare signing headers
        sign_headers = {
            "content-type": request_param["content_type"],
            "host": request_param["host"],
            "x-content-sha256": x_content_sha256,
            "x-date": x_date,
            "servicename": service
        }
        
        signed_headers_str = ";".join(sorted(sign_headers.keys()))
        canonical_headers = "\n".join(f"{k}:{sign_headers[k]}" for k in sorted(sign_headers.keys()))
        
        # Create canonical request
        canonical_request_str = "\n".join([
            request_param["method"].upper(),
            request_param["path"],
            self._norm_query(request_param["query"]),
            canonical_headers,
            "",
            signed_headers_str,
            x_content_sha256
        ])
        
        # Create string to sign
        hashed_canonical_request = self._hash_sha256(canonical_request_str)
        credential_scope = "/".join([short_x_date, self.region, service, "request"])
        string_to_sign = "\n".join(["HMAC-SHA256", x_date, credential_scope, hashed_canonical_request])
        
        # Generate signature
        k_date = self._hmac_sha256(self.secret_key.encode("utf-8"), short_x_date)
        k_region = self._hmac_sha256(k_date, self.region)
        k_service = self._hmac_sha256(k_region, service)
        k_signing = self._hmac_sha256(k_service, "request")
        signature = self._hmac_sha256(k_signing, string_to_sign).hex()
        
        # Create authorization header
        authorization_header = (
            f"HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers_str}, Signature={signature}"
        )
        
        # Prepare final headers
        headers = {
            "Authorization": authorization_header,
            "Content-Type": request_param["content_type"],
            "Host": request_param["host"],
            "X-Content-Sha256": x_content_sha256,
            "X-Date": x_date,
            "ServiceName": service
        }
        
        if additional_headers:
            headers.update(additional_headers)
            
        return headers, request_param
    
    def make_request(self, method: str, service: str, version: str, action: str,
                    query_params: Optional[Dict[str, Any]] = None,
                    body: Optional[str] = None,
                    additional_headers: Optional[Dict[str, str]] = None,
                    timeout: int = 30) -> requests.Response:
        """
        Make a signed request to BytePlus API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            service: BytePlus service name (e.g., 'ecs', 'auto_scaling', 'cloudmonitor')
            version: API version
            action: API action name
            query_params: Query parameters
            body: Request body (for POST requests)
            additional_headers: Additional headers
            timeout: Request timeout in seconds
            
        Returns:
            requests.Response object
        """
        if query_params is None:
            query_params = {}
        
        try:
            headers, request_param = self._sign_request(
                method, service, version, action, query_params, body or "", additional_headers
            )
            
            self.logger.debug(f"Making {method} request to {service} action {action}")
            
            response = requests.request(
                method=method,
                url=f"https://{request_param['host']}{request_param['path']}",
                headers=headers,
                params=request_param["query"],
                data=request_param["body"],
                timeout=timeout
            )
            
            self.logger.debug(f"Response status: {response.status_code}")
            return response
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during request: {e}")
            raise
    
    def make_json_request(self, method: str, service: str, version: str, action: str,
                         query_params: Optional[Dict[str, Any]] = None,
                         json_body: Optional[Dict[str, Any]] = None,
                         additional_headers: Optional[Dict[str, str]] = None,
                         timeout: int = 30) -> Dict[str, Any]:
        """
        Make a signed JSON request to BytePlus API and return parsed JSON response.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            service: BytePlus service name
            version: API version
            action: API action name
            query_params: Query parameters
            json_body: JSON request body (will be serialized)
            additional_headers: Additional headers
            timeout: Request timeout in seconds
            
        Returns:
            Parsed JSON response as dictionary
            
        Raises:
            requests.exceptions.RequestException: For HTTP errors
            json.JSONDecodeError: For JSON parsing errors
            ValueError: For API errors
        """
        body = json.dumps(json_body) if json_body else None
        
        response = self.make_request(
            method, service, version, action, query_params, body, additional_headers, timeout
        )
        
        try:
            response_data = response.json()
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON response: {e}")
            self.logger.error(f"Response content: {response.text}")
            raise
        
        # Check for API errors
        if response.status_code != 200:
            error_msg = f"API request failed with status {response.status_code}"
            if 'ResponseMetadata' in response_data and 'Error' in response_data['ResponseMetadata']:
                error_info = response_data['ResponseMetadata']['Error']
                error_msg += f": {error_info.get('Code', 'Unknown')} - {error_info.get('Message', 'No message')}"
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        
        return response_data