  function click_type = sldclick(h)
% function click_type = sldclick(h) 
% Returns type of slider click for slider with handle h
% requires previous slider value to be stored in userdata
% Usefull information/structure gained from Dave Mellinger, dkm1@cornell.edu .
% uislider.m code
%    returns click type:
%    DWNc  SMALL_DWNc   SAMEc  SMALL_UPc    UPc   DRAGc
%    see vsld_h.m for definitions 
% 
%    Dick Benson, DSP Technology 

%include
% Hearder file with slider mode definitions for islider.mi, sldclickm, and users
%end_include

    sld_max    = get(h,'max');
    sld_min    = get(h,'min');
    d          = sld_max-sld_min;
    
    % normalized units 0...992   992=WEIRDc
    oldval     = 992*(get(h,'userdata')-sld_min)/d;
    newval     = 992*(get(h,'value')-sld_min)/d;
    
                                                        click_type = 20;                           
    if     (newval == oldval),                          click_type = 0;
    elseif (abs(newval - oldval - 9) < 3  ), click_type = 1;
    elseif (abs(newval - oldval - 99  ) < 3  ), click_type = 10;
    elseif (abs(oldval - newval - 9) < 3  ), click_type = -1;
    elseif (abs(oldval - newval - 99  ) < 3  ), click_type = -10;
    else                                          
      if (newval == 992)
        if     (abs(newval - oldval) < 9),         click_type = 1;
        elseif (abs(newval - oldval) < 99  ),         click_type = 10;
        end
      elseif (newval == 0)
        if     (abs(oldval - newval) < 9),         click_type = -1;
        elseif (abs(oldval - newval) < 99  ),         click_type = -10;
        end
      end
    end
% end sldclick function





